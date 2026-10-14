import Lean
import Lean.Util.CollectAxioms
open Lean

namespace OM100Premises

def obj := Json.mkObj
def str (j : Json) (k : String) (d := "") : String := (j.getObjValAs? String k).toOption.getD d
def number (j : Json) (k : String) (d : Nat) : Nat := (j.getObjValAs? Nat k).toOption.getD d
def strings (j : Json) (k : String) : Array String := (j.getObjValAs? (Array String) k).toOption.getD #[]
def err (status reason : String) : Json := obj [("validity_status", toJson status),
  ("metric_status", toJson "not_requested"), ("premise_export_status", toJson "unavailable"),
  ("failure_reason", toJson reason)]

def processOn (state : Elab.Command.State) (text : String) : IO Elab.Frontend.State :=
  Elab.IO.processCommands (Parser.mkInputContext text "candidate.lean") {} state

def requestedImports (request : Json) : Except String (Array Import) := do
  let modules := strings request "imports"
  match request.getObjVal? "import_specs" with
  | .error _ => return modules.map fun n => { module := n.toName }
  | .ok value =>
    let raw ← value.getArr?
    let mut result := #[]
    for row in raw do
      let name ← row.getObjValAs? String "module"
      let metaFlag := (row.getObjValAs? Bool "is_meta").toOption.getD false
      let exported := (row.getObjValAs? Bool "is_exported").toOption.getD true
      let allFlag := (row.getObjValAs? Bool "import_all").toOption.getD false
      result := result.push { module := name.toName, isMeta := metaFlag, isExported := exported, importAll := allFlag : Import }
    if result.map (fun x => x.module.toString) != modules then throw "import_order_mismatch"
    return result

def owner (env : Environment) (n : Name) : Name :=
  if let some idx := env.getModuleIdxFor? n then env.header.moduleNames[idx.toNat]!.toString.toName
  else env.header.mainModule

def mainLibrary (env : Environment) (n : Name) : Bool :=
  let own := owner env n
  (`OAI).isPrefixOf own || own == `EvaluatorCandidate

def importedPrivate (env : Environment) (n : Name) : Bool :=
  isPrivateName n && owner env n != env.header.mainModule

def sortedNames (names : Array Name) : Array Name :=
  (names.qsort (fun a b => a.toString < b.toString)).eraseReps

def sourceScope (state : Elab.Command.State) : IO (Json × String) := do
  let scope := state.scopes.head!
  let current := if scope.currNamespace.isAnonymous then "_root_" else scope.currNamespace.toString
  let opens := scope.openDecls.reverse.map fun entry => match entry with
    | .simple ns except => "open " ++ ns.toString ++
      (if except.isEmpty then "" else " hiding " ++ String.intercalate " " (except.map Name.toString))
    | .explicit id decl => "open-name " ++ id.toString ++ " = " ++ decl.toString
  let mut activeScopes : Array Name := #[]
  for extension in (← scopedEnvExtensionsRef.get) do
    let stack := extension.ext.getState state.env (asyncMode := .local)
    if let top :: _ := stack.stateStack then
      activeScopes := activeScopes ++ top.activeScopes.toArray
  activeScopes := sortedNames activeScopes
  let scopedNames := activeScopes.map Name.toString
  let text := "Active source namespace for the written target: " ++ current ++ "\n" ++
    String.intercalate "\n" opens ++ (if opens.isEmpty then "" else "\n") ++
    (if scopedNames.isEmpty then "" else "Active scoped notation: " ++ String.intercalate ", " scopedNames.toList ++ "\n") ++
    "Use these existing scopes when interpreting unqualified names in the written target.\n\n"
  return (obj [("current_namespace", toJson current), ("resolved_open_declarations", toJson opens),
    ("active_scoped_namespaces", toJson scopedNames),
    ("source", toJson "trusted_pre_target_command_state")], text)

-- New auxiliaries may conceal references to prior helpers. Traverse them for
-- retrieval only. Never export a declaration absent from the pre-target env.
def priorSeeds (base env : Environment) (roots : Array Name) (cap : Nat) : Array Name × Nat := Id.run do
  let mut todo := roots.toList
  let mut seen : NameSet := {}
  let mut prior : Array Name := #[]
  let mut newCount := 0
  while !todo.isEmpty && seen.size < cap do
    let n := todo.head!
    todo := todo.tail!
    if seen.contains n then continue
    seen := seen.insert n
    if (base.find? n).isSome then
      prior := prior.push n
    else
      newCount := newCount + 1
      if let some ci := env.find? n then
        todo := ci.type.getUsedConstants.toList ++ todo
        if let some value := ci.value? (allowOpaque := true) then
          todo := value.getUsedConstants.toList ++ todo
  return (sortedNames prior, newCount)

def kind (ci : ConstantInfo) : String := match ci with
  | .thmInfo _ => "theorem"
  | .axiomInfo _ => "axiom"
  | .defnInfo _ => "def"
  | .opaqueInfo _ => "opaque"
  | .inductInfo _ => "inductive"
  | .ctorInfo _ => "constructor"
  | .recInfo _ => "recursor"
  | .quotInfo _ => "quotient_primitive"

def readableName (n : Name) : String := (privateToUserName n).toString

-- Stop before constructing a gigantic pretty-printed definition only to discard
-- it. Count at most 1,001 expression nodes, without relying on an unbounded walk.
def smallDefinition (value : Expr) : Bool := Id.run do
  let mut todo := [value]
  let mut count := 0
  while !todo.isEmpty && count ≤ 1000 do
    let e := todo.head!
    todo := todo.tail!
    count := count + 1
    match e with
    | .app f a => todo := f :: a :: todo
    | .lam _ t b _ | .forallE _ t b _ => todo := t :: b :: todo
    | .letE _ t v b _ => todo := t :: v :: b :: todo
    | .mdata _ b | .proj _ _ b => todo := b :: todo
    | _ => pure ()
  return todo.isEmpty && count ≤ 1000

def interfaceData (base : Environment) (n : Name) (maxBodyBytes : Nat) : MetaM (Json × String × Array Name) := do
  let some ci := base.find? n | throwError "missing prior declaration"
  let typeText := (← Meta.ppExpr ci.type).pretty
  let universes := if ci.levelParams.isEmpty then "" else
    ".{" ++ String.intercalate ", " (ci.levelParams.map Name.toString) ++ "}"
  let header := s!"{kind ci} {readableName n}{universes} : {typeText}"
  let mut text := header
  let mut dependencies := ci.type.getUsedConstants
  let mut bodyIncluded := false
  let mut bodyOmitted := if mainLibrary base n then "not_a_nonproof_definition" else "external_library_signature_only"
  -- Theorem, opaque and axiom implementations are never even pretty-printed.
  if let .defnInfo info := ci then
    if mainLibrary base n && !(← Meta.isProp ci.type) then
      if info.value.getUsedConstants.any (importedPrivate base) then
        bodyOmitted := "definition_body_uses_imported_private_constants"
      else if !smallDefinition info.value then
        bodyOmitted := "definition_body_exceeds_1000_expression_nodes"
      else
        let valueText := (← Meta.ppExpr info.value).pretty
        if valueText.utf8ByteSize ≤ maxBodyBytes then
          text := header ++ " := " ++ valueText
          bodyIncluded := true
          bodyOmitted := ""
        else bodyOmitted := "definition_body_exceeds_per_declaration_limit"
      -- Ignore proof-valued constants reached only through a definition body;
      -- their proof implementations do not belong in the semantic interface.
      for dep in info.value.getUsedConstants do
        if let some depInfo := base.find? dep then
          if !(← Meta.isProp depInfo.type) then dependencies := dependencies.push dep
  let mut fields : Array String := #[]
  if let some si := getStructureInfo? base n then
    for field in si.fieldInfo do
      fields := fields.push field.projFn.toString
      dependencies := dependencies.push field.projFn
  if let .inductInfo info := ci then
    dependencies := dependencies ++ info.ctors.toArray
  let item := obj [("name", toJson n.toString), ("display_name", toJson (readableName n)),
    ("kind", toJson (kind ci)), ("owner_module", toJson (owner base n).toString),
    ("type_source", toJson typeText), ("universe_parameters", toJson (ci.levelParams.map Name.toString)),
    ("definition_body_included", toJson bodyIncluded), ("body_omission_reason", toJson bodyOmitted),
    ("structure_fields", toJson fields), ("present_before_target", toJson true)]
  return (item, text, sortedNames dependencies)

structure Pending where
  name : Name
  origin : String
  depth : Nat
  library : Bool
  nameText : String
  deriving Inhabited

def mkPending (base : Environment) (n : Name) (origin : String) (depth : Nat) : Pending :=
  { name := n, origin, depth, library := mainLibrary base n, nameText := n.toString }

def pendingLt (a b : Pending) : Bool :=
  if a.origin != b.origin then
    (if a.origin == "target_type" then 0 else if a.origin == "reference_direct" then 1 else 2) <
    (if b.origin == "target_type" then 0 else if b.origin == "reference_direct" then 1 else 2)
  else if a.library != b.library then a.library
  else if a.depth != b.depth then a.depth < b.depth
  else a.nameText < b.nameText

def exportInterfaces (base : Environment) (typeSeeds proofSeeds : Array Name)
    (scopeText : String) (maxContext maxConstants maxBodyBytes : Nat) : MetaM Json := do
  let intro := "Available declarations from the environment before the target.\n" ++
    "Fully quantified signatures include implicit parameters. Definition displays use ⋯ for omitted proof terms.\n" ++
    "Theorem and lemma proof bodies, the target proof, and post-target auxiliaries are not shown.\n\n"
  let mut context := intro ++ scopeText
  if context.utf8ByteSize > maxContext then throwError "Source scope exceeds premise budget"
  let mut queue := (typeSeeds.map fun n => mkPending base n "target_type" 0) ++
    (proofSeeds.map fun n => mkPending base n "reference_direct" 0)
  queue := queue.qsort pendingLt
  let mut seen : NameSet := {}
  let mut queued : NameSet := queue.foldl (fun acc p => acc.insert p.name) {}
  let mut entries : Array Json := #[]
  let mut names : Array String := #[]
  let mut omitted := 0
  let mut privateOmitted := 0
  let mut externalExcluded : NameSet := {}
  let mut failed := 0
  while !queue.isEmpty && seen.size < maxConstants && context.utf8ByteSize + 256 ≤ maxContext do
    let item := queue[0]!
    queue := queue.extract 1 queue.size
    let n := item.name
    if seen.contains n then continue
    seen := seen.insert n
    if (base.find? n).isNone then
      omitted := omitted + 1
      continue
    try
      let (data, rendered, dependencies) ← interfaceData base n maxBodyBytes
      let withSeparator := rendered ++ "\n\n"
      if importedPrivate base n then
        privateOmitted := privateOmitted + 1
      else if context.utf8ByteSize + withSeparator.utf8ByteSize ≤ maxContext then
        context := context ++ withSeparator
        names := names.push n.toString
        entries := entries.push <| obj [("interface", data), ("origin", toJson item.origin),
          ("depth", toJson item.depth), ("rendered_bytes", toJson rendered.utf8ByteSize)]
      else
        omitted := omitted + 1
      let mut added := false
      for dep in dependencies do
        if !seen.contains dep && !queued.contains dep then
          if mainLibrary base dep then
            queued := queued.insert dep
            queue := queue.push (mkPending base dep "interface_dependency" (item.depth + 1))
            added := true
          else
            externalExcluded := externalExcluded.insert dep
      if added then queue := queue.qsort pendingLt
    catch _ =>
      failed := failed + 1
  let outstanding := sortedNames (queue.map (·.name) |>.filter (fun n => !seen.contains n))
  return obj [("premise_context", toJson context), ("source_dep_names", toJson names),
    ("interfaces", toJson entries), ("considered_count", toJson seen.size),
    ("included_count", toJson names.size), ("omitted_count", toJson (omitted + privateOmitted + failed + outstanding.size + externalExcluded.size)),
    ("omitted_for_context_bytes", toJson omitted), ("interface_error_count", toJson failed),
    ("omitted_imported_private_count", toJson privateOmitted),
    ("omitted_external_transitive_count", toJson externalExcluded.size),
    ("transitive_expansion", toJson "OAI_and_source_local_only"),
    ("external_declarations", toJson "direct_signatures_only"),
    ("unvisited_due_to_constant_limit", toJson (if seen.size ≥ maxConstants then outstanding.size else 0)),
    ("unvisited_due_to_context_budget", toJson (if context.utf8ByteSize + 256 > maxContext then outstanding.size else 0)),
    ("definition_expression_node_limit", toJson (1000 : Nat)),
    ("minimum_remaining_context_bytes", toJson (256 : Nat)),
    ("context_bytes", toJson context.utf8ByteSize), ("max_context_bytes", toJson maxContext)]

def run (request : Json) : IO Json := do
  let opts := ({} : Options).set `maxHeartbeats (number request "max_heartbeats" 2000000)
    |>.set `maxRecDepth 10000 |>.set `Elab.async false |>.set `debug.skipKernelTC false
    |>.set `autoImplicit false
  let imports ← IO.ofExcept (requestedImports request)
  let imported ← importModules imports opts (loadExts := true)
  let imported := imported.setMainModule `EvaluatorCandidate
  let contextState ← processOn (Elab.Command.mkState imported {} opts) (str request "context")
  if contextState.commandState.messages.hasErrors then return err "environment_error" "source_context_elaboration_failed"
  let before := contextState.commandState
  let base := before.env.setExporting false
  let (scopeData, scopeText) ← sourceScope before
  let forbidden := strings request "forbidden_declarations"
  for name in forbidden do
    if (base.find? name.toName).isSome then return err "forbidden_environment" "forbidden_target_already_present"
  let rawName := str request "target_name" "OM100PremiseTarget"
  let name := if rawName.startsWith "_root_." then (rawName.drop 7).toString.toName
    else before.scopes.head!.currNamespace ++ rawName.toName
  if (base.find? name).isSome then return err "environment_error" "target_already_present"
  let decl := s!"theorem {rawName} {str request "binders"} :\n{str request "proposition"} :=\n{str request "proof"}"
  let checked ← processOn before decl
  if checked.commandState.messages.hasErrors then return err "elaboration_failure" "reference_elaboration_failed"
  let env := checked.commandState.env.setExporting false
  let some target := env.find? name | return err "target_mismatch" "target_not_found"
  let some value := target.value? (allowOpaque := true) | return err "elaboration_failure" "target_has_no_proof"
  let axioms ← (collectAxioms name : CoreM (Array Name)).toIO' {fileName := "axiom_check", fileMap := default, options := opts} {env := env}
  let permitted := (request.getObjValAs? (Array String) "permitted_axioms").toOption.getD
    #["propext", "Quot.sound", "Classical.choice"]
  if axioms.any (fun n => !permitted.contains n.toString) then return err "disallowed_axiom" "reference_has_disallowed_axioms"
  let (typeSeeds, typeNew) := priorSeeds base env target.type.getUsedConstants 20000
  let (proofSeeds, proofNew) := priorSeeds base env value.getUsedConstants 20000
  let ppOpts := opts.set `pp.fullNames true |>.set `pp.universes true |>.set `pp.funBinderTypes true
    |>.set `pp.proofs false |>.set `pp.proofs.threshold 0 |>.set `pp.proofs.withType false
    |>.set `pp.maxSteps 100000 |>.set `pp.width 100
  let maximum := number request "max_context_bytes" 64000
  if maximum < 512 || maximum > 64000 then return err "evaluator_error" "invalid_context_byte_limit"
  let result ← (exportInterfaces base typeSeeds proofSeeds scopeText maximum
    (min (number request "max_constants" 4096) 4096) (number request "max_definition_bytes" 5000)).run'
    |>.toIO' {fileName := "premise_interface", fileMap := default, options := ppOpts} {env := base}
  return obj [("validity_status", toJson "success"), ("metric_status", toJson "not_requested"),
    ("premise_export_status", toJson "exported"), ("kernel_replay", toJson false),
    ("reference_elaborator_checked", toJson true), ("target", toJson name.toString),
    ("policy", toJson "reference_informed_prior_environment_interfaces_v3"),
    ("reference_informed_retrieval", toJson true), ("proof_bodies_exported", toJson false),
    ("source_scope_snapshot", scopeData),
    ("excluded_post_target_auxiliary_visits", toJson (typeNew + proofNew)),
    ("target_type_prior_seed_count", toJson typeSeeds.size),
    ("reference_proof_prior_seed_count", toJson proofSeeds.size),
    ("export", result)]

end OM100Premises

unsafe def main (args : List String) : IO UInt32 := do
  if args.length != 2 then
    IO.eprintln "Usage: lean --run PremiseExporter.lean REQUEST.json RESULT.json"
    return 2
  enableInitializersExecution
  initSearchPath (← findSysroot)
  let result ← try
    let request ← IO.ofExcept (Json.parse (← IO.FS.readFile args[0]!))
    OM100Premises.run request
  catch e =>
    if (← IO.getEnv "OM100_PREMISE_DEBUG") == some "1" then IO.eprintln e.toString
    pure (OM100Premises.err "evaluator_error" "premise_export_exception")
  IO.FS.writeFile args[1]! (result.pretty ++ "\n")
  return 0
