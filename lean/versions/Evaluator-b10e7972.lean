import Lean
import Lean.Util.CollectAxioms
open Lean
namespace CompressionEval

def str (j : Json) (k : String) (d := "") : String := (j.getObjValAs? String k).toOption.getD d
def strings (j : Json) (k : String) : Array String := (j.getObjValAs? (Array String) k).toOption.getD #[]
def number (j : Json) (k : String) (d : Nat) : Nat := (j.getObjValAs? Nat k).toOption.getD d
-- An absent field is `d`; a present one must be a natural number below 2^64.
def limit? (j : Json) (k : String) (d : Nat) : Option Nat :=
  match j.getObjVal? k with
  | .error _ => some d
  | .ok v => (fromJson? v : Except String Nat).toOption.filter (· < USize.size)
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
  deriving BEq, Hashable, Inhabited

def Span.json (s : Span) : Json := obj [("start", toJson s.start), ("end", toJson s.stop)]
-- Raw byte offset of every normalized offset, built in one pass instead of one scan per leaf.
def originalByteOffsets (source : String) : Array Nat := Id.run do
  let bytes := source.toUTF8
  let mut raw := 0
  let mut offsets := #[0]
  while raw < bytes.size do
    if bytes[raw]! == 13 && raw + 1 < bytes.size && bytes[raw + 1]! == 10 then
      raw := raw + 2
    else raw := raw + 1
    offsets := offsets.push raw
  return offsets

-- Doc comments are comments; repeated identical spans are kept once, in syntax order.
partial def leafSpans (s : Syntax) (acc : Array Span × Std.HashSet Span := (#[], {})) : Array Span × Std.HashSet Span :=
  match s with
  | .node _ k args => if k == ``Parser.Command.docComment then acc else args.foldl (fun acc arg => leafSpans arg acc) acc
  | .atom (.original _ p _ e) _ | .ident (.original _ p _ e) _ _ _ =>
    let span := { start := p.byteIdx, stop := e.byteIdx : Span }
    if span.start == span.stop || acc.2.contains span then acc else (acc.1.push span, acc.2.insert span)
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

def addRef (s : ExprStats) (n : String) (count := 1) : ExprStats := { s with hist := s.hist.insert n (s.hist[n]?.getD 0 + count) }
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

-- Powers of ten by squaring; `Nat.pow` panics on exponents of 2^32 or more.
partial def pow10 (k : Nat) : Nat := if k == 0 then 1 else let r := pow10 (k / 2); if k % 2 == 0 then r * r else 10 * r * r
-- `(toString n).utf8ByteSize` without the decimal conversion, which is quadratic in huge literals.
def decimalBytes (n : Nat) : Nat := Id.run do
  -- 0.301029995663981195213738894724 < log10 2, so `k` starts at most at ⌊log10 n⌋.
  let mut k := n.log2 * 301029995663981195213738894724 / 10 ^ 30
  while pow10 (k + 1) ≤ n do k := k + 1
  return k + 1

def children (e : Expr) : Array Expr :=
  match e with
  | .app f a => #[f, a]
  | .lam _ t b _ | .forallE _ t b _ => #[t, b]
  | .letE _ t v b _ => #[t, v, b]
  | .mdata _ e | .proj _ _ e => #[e]
  | _ => #[]

-- Visit each distinct subterm once: order them children-first, then pass every parent's
-- occurrence count to each of its child positions. Repeated shared expressions keep their
-- multiplicity without materializing or merging a histogram per subterm.
def countExpr (e : Expr) : ExprStats := Id.run do
  let mut index : Std.HashMap Expr Nat := {}
  let mut order : Array Expr := #[]
  let mut stack := #[(e, false)]
  while !stack.isEmpty do
    let (x, expanded) := stack.back!
    stack := stack.pop
    if index.contains x then continue
    if expanded then
      index := index.insert x order.size
      order := order.push x
    else
      stack := stack.push (x, true)
      for c in children x do stack := stack.push (c, false)
  let mut counts := (Array.replicate order.size 0).set! (order.size - 1) 1
  let mut s : ExprStats := {}
  for k in [0:order.size] do
    let i := order.size - 1 - k
    let x := order[i]!
    let n := counts[i]!
    for c in children x do
      let j := index[c]!
      counts := counts.set! j (counts[j]! + n)
    s := { s with nodes := s.nodes + n }
    match x with
    | .const name _ => s := addRef s name.toString n
    | .sort _ => s := addRef s "@Sort" n
    | .mvar _ => s := { s with hasMVar := true }
    | .lit (.natVal v) => s := { s with literalBytes := s.literalBytes + n * decimalBytes v }
    | .lit (.strVal text) => s := { s with literalBytes := s.literalBytes + n * text.utf8ByteSize }
    | _ => pure ()
  return s

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
-- Kernel-check `n` from `source` into `env`, missing dependencies first; `root` gets the trusted type.
-- The state adds up the raw heartbeats each check allocates.
partial def replay (source : Environment) (root : Name) (trusted : ConstantInfo) (heartbeats depth : Nat)
    (n : Name) (env : Environment) (active : NameSet := {}) : ExceptT String (StateT Nat BaseIO) Environment := do
  if (env.find? n).isSome then return env
  if active.contains n then throw s!"cycle while replaying generated declaration {n}"
  let some ci := source.find? n | throw s!"missing generated declaration {n}"
  let mut env := env
  let deps := ci.type.getUsedConstants ++ (ci.value? (allowOpaque := true) |>.map Expr.getUsedConstants |>.getD #[])
  for dep in deps do env ← replay source root trusted heartbeats depth dep env (active.insert n)
  let mut decl ← asDecl ci
  if n == root then
    let some value := ci.value? (allowOpaque := true) | throw "target has no body"
    if ci.levelParams != trusted.levelParams then throw "target universe parameters differ from trusted signature"
    decl := .thmDecl { name := n, levelParams := trusted.levelParams, type := trusted.type, value }
  -- The kernel heartbeat counter is per thread and restarts only in a new task; without one,
  -- every declaration would be charged for all kernel work done before it on this thread.
  -- `IO.getNumHeartbeats` does not restart, so the task reads it on both sides of the check;
  -- matching the result there keeps the second reading after the check. The limits are converted
  -- before the task starts, so the readings cover the check alone.
  let (budget, maxDepth) := (heartbeats.toUSize, depth.toUSize)
  let (result, used) ← IO.wait (← BaseIO.asTask do
    let start ← IO.getNumHeartbeats
    let result : Except String Environment ← match env.addDeclCore budget maxDepth decl none (doCheck := true) with
      | .ok checked => pure (.ok checked)
      | .error .deterministicTimeout => pure (.error s!"kernel deterministic timeout replaying {n}")
      | .error .excessiveMemory | .error .deepRecursion => pure (.error s!"kernel resource limit replaying {n}")
      | .error _ => pure (.error s!"kernel replay rejected {n}")
    return (result, (← IO.getNumHeartbeats) - start))
  modify (· + used)
  liftExcept result
def replayStatus (e : String) : String :=
  if e.startsWith "kernel deterministic timeout" then "verification_timeout"
  else if e.startsWith "kernel resource limit" then "verification_resource_limit" else "kernel_replay_failure"
def processOn (state : Elab.Command.State) (text : String) : IO Elab.Frontend.State :=
  Elab.IO.processCommands (Parser.mkInputContext text "candidate.lean") {} state
-- `processOn` elaborates in a task on a pooled thread whose `IO.getNumHeartbeats` counter keeps earlier
-- tasks' counts. Imported environments copy the builtin elaborator table, so this entry is added before
-- imports; under the builtin's own name (incrementality and info trees unchanged) it is tried first and
-- records each declaration's elaboration delta on that thread.
def meterDeclarations (used : IO.Ref (Option Nat)) : IO Unit :=
  Elab.Command.commandElabAttribute.addBuiltin ``Parser.Command.declaration ``Elab.Command.elabDeclaration fun stx => do
    let start ← IO.getNumHeartbeats
    try Elab.Command.elabDeclaration stx finally used.set ((← IO.getNumHeartbeats) - start)
-- Raw heartbeats, and in `maxHeartbeats` units (thousands of them).
def heartbeatsJson (elaboration : Option Nat) (kernel : Nat) : Json := match elaboration with
  | none => Json.null
  | some e =>
    let phases := [("elaboration", e), ("kernel_replay", kernel), ("total", e + kernel)]
    obj [("raw", obj (phases.map fun (k, n) => (k, toJson n))),
      ("max_heartbeats_units", obj (phases.map fun (k, n) => (k, Json.num { mantissa := n, exponent := 3 })))]

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

def importFlag (spec : Json) (key : String) (defaultValue : Bool) : Except String Bool :=
  match spec.getObjVal? key with
  | .error _ => .ok defaultValue
  | .ok value => fromJson? value

def requestedImports (request : Json) : Except String (Array Import) := do
  let modules := strings request "imports"
  match request.getObjVal? "import_specs" with
  | .error _ => return modules.map fun name => { module := name.toName }
  | .ok raw =>
    let specs ← raw.getArr?
    let mut result : Array Import := #[]
    for spec in specs do
      let moduleName ← spec.getObjValAs? String "module"
      if moduleName.isEmpty then throw "import_specs module must be nonempty"
      let metaFlag ← importFlag spec "is_meta" false
      let exportFlag ← importFlag spec "is_exported" true
      let allFlag ← importFlag spec "import_all" false
      result := result.push { module := moduleName.toName, isMeta := metaFlag, isExported := exportFlag, importAll := allFlag }
    if result.map (fun item => item.module.toString) != modules then
      throw "import_specs module order must match imports exactly"
    return result

def run (request : Json) : IO Json := do
  let elaboration ← IO.mkRef none
  meterDeclarations elaboration
  let opts := ({} : Options).set `maxHeartbeats (number request "max_heartbeats" 2000000)
    |>.set `Elab.async false |>.set `debug.skipKernelTC false
  -- Each replayed declaration gets the kernel budget Lean.addDecl derives from these options, and
  -- recursion depth 10000. The overrides are for tests and diagnostics; the Python runner never sends them.
  let some budget := limit? request "replay_max_heartbeats" (Core.getMaxHeartbeats opts)
    | return err "evaluator_error" "replay_max_heartbeats must be a natural number below 2^64"
  let some depth := limit? request "replay_max_rec_depth" 10000
    | return err "evaluator_error" "replay_max_rec_depth must be a natural number below 2^64"
  let imports ← match requestedImports request with
    | .ok value => pure value
    | .error message => return err "environment_error" message
  let env ← importModules imports opts (loadExts := true)
  let env := env.setMainModule `EvaluatorCandidate
  let contextState ← processOn (Elab.Command.mkState env {} opts) (str request "context")
  if contextState.commandState.messages.hasErrors then return err "environment_error" (← messagesText contextState.commandState.messages)
  let base := contextState.commandState
  let forbidden := strings request "forbidden_declarations"
  if !forbidden.isEmpty then
    let forbiddenSet := forbidden.foldl (fun set label => set.insert label) ({} : Std.HashSet String)
    let present := base.env.constants.fold (fun found name _ =>
      if forbiddenSet.contains name.toString then found.push name.toString else found) (#[] : Array String)
    if !present.isEmpty then return err "forbidden_environment" s!"forbidden declaration already in runtime environment: {String.intercalate ", " present.toList}"
  let rawName := str request "target_name" "compression_target"
  let targetName := if rawName.startsWith "_root_." then (rawName.drop 7).toString.toName else base.scopes.head!.currNamespace ++ rawName.toName
  if (base.env.find? targetName).isSome then return err "environment_error" "target already exists in trusted environment"
  let binders := str request "binders"
  let prop := str request "proposition"
  -- A missing response is a checker input error, never the model's parse failure.
  let some proof := (request.getObjValAs? String "proof").toOption | return err "evaluator_error" "request proof must be a string"
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
  let offsets := originalByteOffsets decl
  let original := fun (p : Nat) => offsets[p]?.getD decl.utf8ByteSize
  let rawSpans := (leafSpans declSyntax).1.map (fun p => { start := original p.start, stop := original p.stop : Span })
  let spans := rawSpans.qsort (fun a b => a.start < b.start)
  let ss := spans.filter (fun s => (sigStart ≤ s.start && s.stop ≤ bindersEnd) || (propStart ≤ s.start && s.stop ≤ sigEnd))
  let bs := spans.filter (fun s => proofStart ≤ s.start)
  let trustedName := base.scopes.head!.currNamespace ++ `__compression_trusted_signature
  let trustedState ← processOn base s!"axiom __compression_trusted_signature {binders} :\n{prop}"
  if trustedState.commandState.messages.hasErrors then return err "environment_error" (← messagesText trustedState.commandState.messages)
  let some trusted := trustedState.commandState.env.find? trustedName | return err "environment_error" "trusted signature missing"
  elaboration.set none
  let checked ← processOn base decl
  let elaborated ← elaboration.get
  let env := checked.commandState.env.setExporting false
  if checked.commandState.messages.hasErrors then return err "elaboration_failure" (← messagesText checked.commandState.messages)
  let some target := env.find? targetName | return err "target_mismatch" "candidate target missing"
  -- Each fork elaborates its own statement auxiliaries (e.g. matchers): the trusted ones are checked
  -- in first, so candidate names cannot shadow them, then those the candidate's type uses.
  -- Comparison and replay start there.
  let trustedEnv := trustedState.commandState.env.setExporting false
  let trustedBase ← match ← (trusted.type.getUsedConstants.foldlM (fun e n => replay trustedEnv targetName trusted budget depth n e) base.env).run.run' 0 with
    | .ok value => pure value
    | .error e => return err "environment_error" e
  -- Kernel heartbeats count the candidate's declarations only.
  let (comparable, kernelBeats) ← (target.type.getUsedConstants.foldlM (fun e n => replay env targetName trusted budget depth n e) trustedBase).run.run 0
  let comparable ← match comparable with
    | .ok value => pure value
    | .error e => return err (replayStatus e) e
  if !(Kernel.isDefEqGuarded comparable {} target.type trusted.type) then return err "target_mismatch" "elaborated target differs from trusted signature"
  -- Both forks' statement auxiliaries: elaborated from the fixed statement, never by the proof.
  let statementAuxiliaries := comparable.constants.foldStage2 (fun acc n _ => if (base.env.find? n).isNone then acc.push n.toString else acc) (#[] : Array String)
  let (axioms, closureSize) ← match auditClosure env targetName forbidden with
    | .ok result => pure result
    | .error e => return err (if e.startsWith "forbidden" then "forbidden_dependency" else "validation_error") e
  let permitted := (request.getObjValAs? (Array String) "permitted_axioms").toOption.getD #["propext", "Classical.choice", "Quot.sound"]
  let disallowed := axioms.filter (fun n => n == "sorryAx" || !permitted.contains n)
  if !disallowed.isEmpty then return err "disallowed_axiom" (String.intercalate ", " disallowed.toList)
  let (replayed, kernelBeats) ← (replay env targetName trusted budget depth targetName comparable).run.run kernelBeats
  if let .error e := replayed then return err (replayStatus e) e
  let tokens := obj [("W_S", toJson ss.size), ("W_B", toJson bs.size), ("W_full", toJson spans.size),
    ("signature_spans", toJson (ss.map Span.json)), ("body_spans", toJson (bs.map Span.json)),
    ("full_spans", toJson (spans.map Span.json)), ("span_unit", toJson "utf8_bytes"),
    ("source", toJson decl), ("proof_offset", toJson proofStart), ("policy", toJson "lean_original_leaf_spans_v1")]
  let prettyType ← (Meta.ppExpr trusted.type).run' |>.toIO' { fileName := "trusted", fileMap := default, options := opts.set `pp.fullNames true |>.set `pp.universes true |>.set `pp.funBinderTypes true } { env := trustedState.commandState.env }
  let telescope ← (telescopeJson trusted.type).run' |>.toIO' { fileName := "trusted", fileMap := default, options := opts.set `pp.fullNames true |>.set `pp.universes true |>.set `pp.funBinderTypes true } { env := trustedState.commandState.env }
  let generated := env.constants.foldStage2 (fun acc n _ => if (base.env.find? n).isNone then acc.push n.toString else acc) (#[] : Array String)
  let basic := [("validity_status", toJson "success"), ("root", toJson targetName.toString),
    ("axioms", toJson axioms), ("kernel_replay", toJson true), ("heartbeats", heartbeatsJson elaborated kernelBeats),
    ("candidate_declarations", toJson generated),
    ("statement_auxiliaries", toJson (statementAuxiliaries.qsort (· < ·))),
    ("resolved_imports", toJson (imports.map fun item => obj [("module", toJson item.module.toString),
      ("is_meta", toJson item.isMeta), ("is_exported", toJson item.isExported), ("import_all", toJson item.importAll)])),
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
