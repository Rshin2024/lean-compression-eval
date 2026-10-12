

theorem _root_.CompressionEvalSignatureOriginal  : (ep.subtypeCongr en).trans (ep'.subtypeCongr en')
    = Perm.subtypeCongr (ep.trans ep') (en.trans en') := by
  grind [Equiv.Perm.subtypeCongr.eq_def, coe_trans]

theorem _root_.CompressionEvalSignatureMaterialized {«ε» : Type u_9} {«p» : ε → Prop} [«inst» : DecidablePred.{u_9 + 1} p] («ep» : Equiv.Perm.{u_9 + 1} (Subtype.{u_9 + 1} fun (a : ε) => p a)) («ep'» : Equiv.Perm.{u_9 + 1} (Subtype.{u_9 + 1} fun (a : ε) => p a)) («en» : Equiv.Perm.{u_9 + 1} (Subtype.{u_9 + 1} fun (a : ε) => ¬p a)) («en'» : Equiv.Perm.{u_9 + 1} (Subtype.{u_9 + 1} fun (a : ε) => ¬p a)) : (ep.subtypeCongr en).trans (ep'.subtypeCongr en')
    = Perm.subtypeCongr (ep.trans ep') (en.trans en') := by
  grind [Equiv.Perm.subtypeCongr.eq_def, coe_trans]

run_cmd do
  let env ← Lean.getEnv
  let Option.some old := env.find? `CompressionEvalSignatureOriginal | throwError "missing original comparison declaration"
  let Option.some fresh := env.find? `CompressionEvalSignatureMaterialized | throwError "missing materialized comparison declaration"
  unless old.levelParams.length == fresh.levelParams.length do throwError "universe parameter count changed"
  let oldType := old.type.instantiateLevelParams old.levelParams (fresh.levelParams.map Lean.Level.param)
  unless Lean.Kernel.isDefEqGuarded env {} oldType fresh.type do throwError "materialized signature differs from original"
