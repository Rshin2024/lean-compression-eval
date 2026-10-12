import Lean
import Lean.Util.CollectAxioms
open Lean
namespace CompressionEval

def str (j : Json) (k : String) (d := "") : String := (j.getObjValAs? String k).toOption.getD d
def strings (j : Json) (k : String) : Array String := (j.getObjValAs? (Array String) k).toOption.getD #[]
def number (j : Json) (k : String) (d : Nat) : Nat := (j.getObjValAs? Nat k).toOption.getD d
def obj := Json.mkObj
def err (s reason : String) : Json := obj [("validity_status", toJson s), ("metric_status", toJson "unavailable"), ("failure_reason", toJson reason)]
def messagesText (m : MessageLog) : IO String := do
  return String.intercalate "\n" (← m.toList.mapM fun x => x.toString)

-- Parse the entire category; later remap Lean-normalized CRLF positions to raw bytes.
def parseOriginal (env : Environment) (category : Name) (source : String) : Except String Syntax :=
  let parser := Parser.andthenFn Parser.whitespace (Parser.categoryParserFnImpl category)
  let ctx := Parser.mkInputContext source "candidate.lean" (normalizeLineEndings := true)
  let state := parser.run ctx { env, options := {} } (Parser.getTokenTable env) (Parser.mkParserState source)
  if !state.allErrors.isEmpty then .error (state.toErrorMsg ctx)
  else if ctx.atEnd state.pos then .ok state.stxStack.back
  else .error ((state.mkError "end of input").toErrorMsg ctx)

structure Span where
  start : Nat
  stop : Nat
  deriving BEq, Inhabited

def Span.json (s : Span) : Json := obj [("start", toJson s.start), ("end", toJson s.stop)]
def originalByteOffset (source : String) (offset : Nat) : Nat := Id.run do
  let bytes := source.toUTF8
  let mut raw := 0
  let mut normalized := 0
  while normalized < offset && raw < bytes.size do
    if bytes[raw]! == 13 && raw + 1 < bytes.size && bytes[raw + 1]! == 10 then
      raw := raw + 2
    else raw := raw + 1
    normalized := normalized + 1
  return raw

partial def leafSpans (s : Syntax) (acc : Array Span := #[]) : Array Span :=
  match s with
  | .node _ _ args => args.foldl (fun acc arg => leafSpans arg acc) acc
  | .atom (.original _ p _ e) _ | .ident (.original _ p _ e) _ _ _ =>
    let span := { start := p.byteIdx, stop := e.byteIdx : Span }
    if span.start == span.stop || acc.contains span then acc else acc.push span
  | _ => acc
partial def syntaxKinds (s : Syntax) (acc : Array String := #[]) : Array String :=
  match s with
  | .node _ kind args => args.foldl (fun acc a => syntaxKinds a acc) (if acc.contains kind.toString then acc else acc.push kind.toString)
  | _ => acc
-- Defense in depth; the caller MUST isolate elaboration at the operating-system level.
def forbiddenWords : Array String := #["run_tac", "run_elab", "run_cmd", "elab", "elab_rules", "by_elab", "macro", "macro_rules", "unsafe", "native_decide", "native_cast", "set_option", "unhygienic", "implemented_by", "extern", "eval_expr", "eval%"]
partial def restrictedSyntax? (s : Syntax) : Option String :=
  match s with
  | .node _ k args =>
    if k.toString.toLower.contains "runtac" || k.toString.toLower.contains "runelab" then some k.toString
    else args.foldl (fun acc a => acc.orElse fun _ => restrictedSyntax? a) none
  | .atom _ a => if forbiddenWords.contains a then some a else none
  | .ident _ _ n _ =>
    let name := n.toString
    if forbiddenWords.contains name || #["Lean.", "IO.", "System."].any (fun pre => name.startsWith pre) then some name else none
  | _ => none

partial def containsAdmission (s : Syntax) : Bool :=
  match s with
  | .node _ k args => k.toString.endsWith ".sorry" || k.toString.endsWith ".admit" || args.any containsAdmission
  | .atom _ a => a == "sorry" || a == "admit"
  | _ => false

abbrev Hist := Std.HashMap String Nat
structure ExprStats where
  hist : Hist := {}
  nodes : Nat := 0
  literalBytes : Nat := 0
  hasMVar : Bool := false
  deriving Inhabited

def addRef (s : ExprStats) (n : String) : ExprStats := { s with hist := s.hist.insert n (s.hist[n]?.getD 0 + 1) }
-- Every expression occurrence is visited, even when pointers are shared.
partial def countExprUncached (e : Expr) (s : ExprStats := {}) : ExprStats :=
  let s := { s with nodes := s.nodes + 1 }
  match e with
  | .const n _ => addRef s n.toString
  | .sort _ => addRef s "@Sort"
  | .app f a => countExprUncached a (countExprUncached f s)
  | .lam _ t b _ | .forallE _ t b _ => countExprUncached b (countExprUncached t s)
  | .letE _ t v b _ => countExprUncached b (countExprUncached v (countExprUncached t s))
  | .mdata _ e | .proj _ _ e => countExprUncached e s
  | .mvar _ => { s with hasMVar := true }
  | .lit (.natVal n) => { s with literalBytes := s.literalBytes + (toString n).utf8ByteSize }
  | .lit (.strVal text) => { s with literalBytes := s.literalBytes + text.utf8ByteSize }
  | _ => s

def mergeStats (a b : ExprStats) : ExprStats :=
  { hist := b.hist.fold (fun acc n count => acc.insert n (acc[n]?.getD 0 + count)) a.hist,
    nodes := a.nodes + b.nodes, literalBytes := a.literalBytes + b.literalBytes,
    hasMVar := a.hasMVar || b.hasMVar }

-- Cache each subtree's complete histogram; merge it at every occurrence.
partial def cachedExpr (e : Expr) : StateM (Std.HashMap Expr ExprStats) ExprStats := do
  if let some result := (← get)[e]? then return result
  let start : ExprStats := { nodes := 1 }
  let result ← match e with
    | .const n _ => pure (addRef start n.toString)
    | .sort _ => pure (addRef start "@Sort")
    | .app f a => return mergeStats start (mergeStats (← cachedExpr f) (← cachedExpr a))
    | .lam _ t b _ | .forallE _ t b _ => return mergeStats start (mergeStats (← cachedExpr t) (← cachedExpr b))
    | .letE _ t v b _ => return mergeStats start (mergeStats (← cachedExpr t) (mergeStats (← cachedExpr v) (← cachedExpr b)))
    | .mdata _ e | .proj _ _ e => return mergeStats start (← cachedExpr e)
    | .mvar _ => pure { start with hasMVar := true }
    | .lit (.natVal n) => pure { start with literalBytes := (toString n).utf8ByteSize }
    | .lit (.strVal text) => pure { start with literalBytes := text.utf8ByteSize }
    | _ => pure start
  modify (·.insert e result)
  return result

def countExpr (e : Expr) : ExprStats := cachedExpr e |>.run' {}

def histJson (h : Hist) : Json := obj <| (h.toList.mergeSort (fun a b => a.1 ≤ b.1)).map fun (k,v) => (k, toJson v)
def exprHash (e : Expr) : String := toString (hash e)
def nodeJson (name owner : String) (primitive : Bool) (ci : ConstantInfo) : Json :=
  if primitive then
    obj [("name", toJson name), ("owner_module", toJson owner), ("primitive", toJson true),
      ("histogram_status", toJson "atomic_boundary_not_expanded"),
      ("body_present", toJson (ci.value? (allowOpaque := true)).isSome),
      ("signature", obj []), ("body", Json.null), ("source_counts", Json.null),
      ("type_hash", toJson (exprHash ci.type)),
      ("value_hash", toJson (ci.value? (allowOpaque := true) |>.map exprHash)),
      ("type_nodes", Json.null), ("body_nodes", Json.null), ("literal_bytes", Json.null)]
  else
  let t := countExpr ci.type
  let b := ci.value? (allowOpaque := true) |>.map countExpr
  obj [("name", toJson name), ("owner_module", toJson owner), ("primitive", toJson primitive),
    ("histogram_status", toJson "measured"), ("body_present", toJson b.isSome),
    ("signature", histJson t.hist), ("body", b.map (histJson ·.hist) |>.getD Json.null),
    ("type_hash", toJson (exprHash ci.type)), ("value_hash", toJson (ci.value? (allowOpaque := true) |>.map exprHash)),
    ("type_nodes", toJson t.nodes), ("body_nodes", toJson (b.map (·.nodes))),
    ("literal_bytes", toJson (t.literalBytes + (b.map (·.literalBytes)).getD 0)), ("source_counts", Json.null)]
def owner? (env : Environment) (name : Name) : Option Name :=
  if let some idx := env.getModuleIdxFor? name then env.header.moduleNames[idx.toNat]?
  else if (env.find? name).isSome then some env.header.mainModule else none
def isExpanded (owner : Name) (extra : Array String) : Bool :=
  (`Mathlib).isPrefixOf owner || owner == `EvaluatorCandidate || extra.contains owner.toString

partial def graph (env : Environment) (root : Name) (extra : Array String) (cap : Nat) : Except String (Array Json) := do
  let rec visit (todo : List Name) (seen : NameSet) (labels : Std.HashMap String Name) (nodes : Array Json) : Except String (Array Json) := do
    match todo with
    | [] => return nodes
    | n :: rest =>
      if seen.contains n then visit rest seen labels nodes else
      if nodes.size ≥ cap then throw s!"closure exceeds max_graph_nodes={cap}" else
      let some ci := env.find? n | throw s!"unindexed dependency: {n}"
      let some own := owner? env n | throw s!"missing module ownership: {n}"
      if let some previous := labels[n.toString]? then
        if previous != n then throw s!"declaration label collision: {n}"
      let primitive := !isExpanded own extra
      let item := nodeJson n.toString own.toString primitive ci
      let mut deps : List Name := []
      let mut hasSort := false
      if !primitive then
        deps := ci.type.getUsedConstants.toList
        hasSort := ((item.getObjValD "signature").getObjVal? "@Sort").isOk
        if let some v := ci.value? (allowOpaque := true) then
          deps := deps ++ v.getUsedConstants.toList
          hasSort := hasSort || ((item.getObjValD "body").getObjVal? "@Sort").isOk
      let nodes := nodes.push item
      let nodes := if hasSort && !nodes.any (fun j => str j "name" == "@Sort") then nodes.push (obj [
        ("name", toJson "@Sort"), ("primitive", toJson true), ("owner_module", toJson "@builtin"),
        ("signature", obj []), ("body", Json.null), ("type_hash", toJson "@Sort"), ("value_hash", Json.null)]) else nodes
      visit (deps ++ rest) (seen.insert n) (labels.insert n.toString n) nodes
  visit [root] {} {} #[]

-- Audit all packages transitively, independently of the metric primitive boundary.
partial def auditClosure (env : Environment) (root : Name) (forbidden : Array String) : Except String (Array String × Nat) := do
  let rec visit (todo : List Name) (seen : NameSet) (axioms : Array String) : Except String (Array String × Nat) := do
    match todo with
    | [] => return (axioms.qsort (· < ·), seen.size)
    | n :: rest =>
      if seen.contains n then visit rest seen axioms else
      if forbidden.contains n.toString then throw s!"forbidden dependency: {n}" else
      let some ci := env.find? n | throw s!"missing audit dependency: {n}"
      if ci.type.hasMVar || (ci.value? (allowOpaque := true)).any Expr.hasMVar then
        throw s!"unresolved metavariable in {n}"
      let axs := if ci matches .axiomInfo _ then axioms.push n.toString else axioms
      let mut deps := ci.type.getUsedConstants.toList
      if let some value := ci.value? (allowOpaque := true) then deps := deps ++ value.getUsedConstants.toList
      if let .inductInfo val := ci then deps := deps ++ val.ctors
      visit (deps ++ rest) (seen.insert n) axs
  visit [root] {} #[]
def asDecl (ci : ConstantInfo) : Except String Declaration :=
  match ci with
  | .defnInfo v => .ok (.defnDecl v)
  | .thmInfo v => .ok (.thmDecl v)
  | .opaqueInfo v => .ok (.opaqueDecl v)
  | .axiomInfo _ => .error s!"candidate introduced axiom {ci.name}"
  | _ => .error s!"unsupported generated declaration in kernel replay: {ci.name}"
partial def replay (base final : Environment) (root : Name) (trusted : ConstantInfo) (heartbeats : Nat) : Except String Environment := do
  let rec visit (n : Name) (env : Environment) (active : NameSet) : Except String Environment := do
    if (env.find? n).isSome then return env
    if active.contains n then throw s!"cycle while replaying generated declaration {n}"
    let some ci := final.find? n | throw s!"missing generated declaration {n}"
    let mut env := env
    let deps := ci.type.getUsedConstants ++ (ci.value? (allowOpaque := true) |>.map Expr.getUsedConstants |>.getD #[])
    for dep in deps do env ← visit dep env (active.insert n)
    let mut decl ← asDecl ci
    if n == root then
      let some value := ci.value? (allowOpaque := true) | throw "target has no body"
      if ci.levelParams != trusted.levelParams then throw "target universe parameters differ from trusted signature"
      decl := .thmDecl { name := n, levelParams := trusted.levelParams, type := trusted.type, value }
    match env.addDeclCore heartbeats.toUSize 10000 decl none (doCheck := true) with
    | .ok checked => return checked
    | .error _ => throw s!"kernel replay rejected {n}"
  visit root base {}
def processOn (state : Elab.Command.State) (text : String) : IO Elab.Frontend.State :=
  Elab.IO.processCommands (Parser.mkInputContext text "candidate.lean") {} state

def binderKind (b : BinderInfo) : String := match b with
  | .default => "explicit"
  | .implicit => "implicit"
  | .strictImplicit => "strict_implicit"
  | .instImplicit => "instance"

def telescopeJson (type : Expr) : MetaM Json := Meta.forallTelescope type fun vars body => do
  let mut binders : Array Json := #[]
  for var in vars do
    let decl ← var.fvarId!.getDecl
    let typeText ← Meta.ppExpr decl.type
    binders := binders.push <| obj [("name", toJson decl.userName.toString),
      ("binder_info", toJson (binderKind decl.binderInfo)), ("type_source", toJson typeText.pretty)]
  let bodyText ← Meta.ppExpr body
  return obj [("binders", toJson binders), ("proposition", toJson bodyText.pretty)]

def run (request : Json) : IO Json := do
  let opts := ({} : Options).set `maxHeartbeats (number request "max_heartbeats" 2000000)
    |>.set `Elab.async false |>.set `debug.skipKernelTC false
  let env ← importModules ((strings request "imports").map fun s => { module := s.toName }) opts (loadExts := true)
  let env := env.setMainModule `EvaluatorCandidate
  let contextState ← processOn (Elab.Command.mkState env {} opts) (str request "context")
  if contextState.commandState.messages.hasErrors then return err "environment_error" (← messagesText contextState.commandState.messages)
  let base := contextState.commandState
  let forbidden := strings request "forbidden_declarations"
  for name in forbidden do
    if (base.env.find? name.toName).isSome then return err "forbidden_environment" s!"forbidden declaration already in runtime environment: {name}"
  let rawName := str request "target_name" "compression_target"
  let targetName := if rawName.startsWith "_root_." then (rawName.drop 7).toString.toName else base.scopes.head!.currNamespace ++ rawName.toName
  if (base.env.find? targetName).isSome then return err "environment_error" "target already exists in trusted environment"
  let binders := str request "binders"
  let prop := str request "proposition"
  let proof := str request "proof"
  let proofSyntax ← match parseOriginal base.env `term proof with
    | .ok s => pure s
    | .error e => return err "parse_failure" e
  if containsAdmission proofSyntax then return err "disallowed_axiom" "source contains sorry/admit, including discarded local proofs"
  if let some bad := restrictedSyntax? proofSyntax then return err "output_format_failure" s!"restricted executable metaprogramming syntax: {bad}"
  let sigPrefix := s!"theorem {rawName} "
  let sig := binders ++ " :\n  " ++ prop
  let decl := sigPrefix ++ sig ++ " :=\n" ++ proof
  let declSyntax ← match parseOriginal base.env `command decl with
    | .ok s => pure s
    | .error e => return err "parse_failure" e
  let sigStart := sigPrefix.utf8ByteSize
  let bindersEnd := sigStart + binders.utf8ByteSize
  let propStart := bindersEnd + " :\n  ".utf8ByteSize
  let sigEnd := sigStart + sig.utf8ByteSize
  let proofStart := sigEnd + " :=\n".utf8ByteSize
  let rawSpans := (leafSpans declSyntax).map (fun p => { start := originalByteOffset decl p.start, stop := originalByteOffset decl p.stop : Span })
  let spans := rawSpans.qsort (fun a b => a.start < b.start)
  let ss := spans.filter (fun s => (sigStart ≤ s.start && s.stop ≤ bindersEnd) || (propStart ≤ s.start && s.stop ≤ sigEnd))
  let bs := spans.filter (fun s => proofStart ≤ s.start)
  let trustedName := base.scopes.head!.currNamespace ++ `__compression_trusted_signature
  let trustedState ← processOn base s!"axiom __compression_trusted_signature {binders} :\n{prop}"
  if trustedState.commandState.messages.hasErrors then return err "environment_error" (← messagesText trustedState.commandState.messages)
  let some trusted := trustedState.commandState.env.find? trustedName | return err "environment_error" "trusted signature missing"
  let checked ← processOn base decl
  let env := checked.commandState.env.setExporting false
  if checked.commandState.messages.hasErrors then return err "elaboration_failure" (← messagesText checked.commandState.messages)
  let some target := env.find? targetName | return err "target_mismatch" "candidate target missing"
  if !(Kernel.isDefEqGuarded env {} target.type trusted.type) then return err "target_mismatch" "elaborated target differs from trusted signature"
  let (axioms, closureSize) ← match auditClosure env targetName forbidden with
    | .ok result => pure result
    | .error e => return err (if e.startsWith "forbidden" then "forbidden_dependency" else "validation_error") e
  let permitted := (request.getObjValAs? (Array String) "permitted_axioms").toOption.getD #["propext", "Classical.choice", "Quot.sound"]
  let disallowed := axioms.filter (fun n => n == "sorryAx" || !permitted.contains n)
  if !disallowed.isEmpty then return err "disallowed_axiom" (String.intercalate ", " disallowed.toList)
  match replay base.env env targetName trusted (number request "max_heartbeats" 2000000) with
  | .error e => return err "kernel_replay_failure" e
  | .ok _ => pure ()
  let tokens := obj [("W_S", toJson ss.size), ("W_B", toJson bs.size), ("W_full", toJson spans.size),
    ("signature_spans", toJson (ss.map Span.json)), ("body_spans", toJson (bs.map Span.json)),
    ("full_spans", toJson (spans.map Span.json)), ("span_unit", toJson "utf8_bytes"),
    ("source", toJson decl), ("proof_offset", toJson proofStart), ("policy", toJson "lean_original_leaf_spans_v1")]
  let prettyType ← (Meta.ppExpr trusted.type).run' |>.toIO' { fileName := "trusted", fileMap := default, options := opts.set `pp.fullNames true |>.set `pp.universes true |>.set `pp.funBinderTypes true } { env := trustedState.commandState.env }
  let telescope ← (telescopeJson trusted.type).run' |>.toIO' { fileName := "trusted", fileMap := default, options := opts.set `pp.fullNames true |>.set `pp.universes true |>.set `pp.funBinderTypes true } { env := trustedState.commandState.env }
  let generated := env.constants.foldStage2 (fun acc n _ => if (base.env.find? n).isNone then acc.push n.toString else acc) (#[] : Array String)
  let basic := [("validity_status", toJson "success"), ("root", toJson targetName.toString),
    ("axioms", toJson axioms), ("kernel_replay", toJson true), ("candidate_declarations", toJson generated),
    ("proved_type_fingerprint", toJson (exprHash target.type)), ("fingerprint_kind", toJson "lean_expr_hash_noncryptographic"),
    ("trusted_type_source", toJson prettyType.pretty), ("trusted_type_expression", toJson (toString target.type)),
    ("universe_parameters", toJson (target.levelParams.map Name.toString)), ("trusted_telescope", telescope), ("source_tokens", tokens),
    ("diagnostics", obj [("transitive_dependency_count", toJson closureSize),
      ("proof_nodes", toJson ((countExpr (target.value? (allowOpaque := true)).get!).nodes)),
      ("syntax_kinds", toJson (syntaxKinds proofSyntax)),
      ("cached_uncached_root_histogram_agree", if (request.getObjValAs? Bool "verify_histogram_cache").toOption.getD false then toJson (histJson (countExpr (target.value? (allowOpaque := true)).get!).hist == histJson (countExprUncached (target.value? (allowOpaque := true)).get!).hist) else Json.null)])]
  if str request "mode" == "validate" then return obj (basic ++ [("metric_status", toJson "not_requested")])
  match graph env targetName (strings request "expand_modules") (number request "max_graph_nodes" 100000) with
  | .ok nodes => return obj (basic ++ [("metric_status", toJson "available"), ("nodes", toJson nodes)])
  | .error e => return obj (basic ++ [("metric_status", toJson "unavailable"), ("metric_error", toJson e)])
end CompressionEval

unsafe def main (args : List String) : IO UInt32 := do
  if args.length != 2 then
    IO.eprintln "Usage: lean --run Evaluator.lean REQUEST.json RESULT.json"
    return 2
  enableInitializersExecution
  initSearchPath (← findSysroot)
  let result ← try
    let input ← IO.FS.readFile args[0]!
    let request ← IO.ofExcept (Json.parse input)
    CompressionEval.run request
  catch e => pure (CompressionEval.err "evaluator_error" e.toString)
  IO.FS.writeFile args[1]! (result.pretty ++ "\n")
  return 0
