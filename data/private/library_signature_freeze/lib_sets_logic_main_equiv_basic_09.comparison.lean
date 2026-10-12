

theorem _root_.CompressionEvalSignatureOriginal (x : { a // ¬p a } → β) (a : α) (h : p a) : ((subtypePreimage p x₀).symm x : α → β) a = x₀ ⟨a, h⟩ := dite_eq_left h

theorem _root_.CompressionEvalSignatureMaterialized {«α» : Sort u_1} {«β» : Sort u_4} («p» : α → Prop) [«inst» : DecidablePred.{u_1} p] («x₀» : (Subtype.{u_1} fun (a : α) => p a) → β) (x : { a // ¬p a } → β) (a : α) (h : p a) : ((subtypePreimage p x₀).symm x : α → β) a = x₀ ⟨a, h⟩ := dite_eq_left h

run_cmd do
  let env ← Lean.getEnv
  let some old := env.find? `CompressionEvalSignatureOriginal | throwError "missing original comparison declaration"
  let some fresh := env.find? `CompressionEvalSignatureMaterialized | throwError "missing materialized comparison declaration"
  unless old.levelParams.length == fresh.levelParams.length do throwError "universe parameter count changed"
  let oldType := old.type.instantiateLevelParams old.levelParams (fresh.levelParams.map Lean.Level.param)
  unless Lean.Kernel.isDefEqGuarded env {} oldType fresh.type do throwError "materialized signature differs from original"
