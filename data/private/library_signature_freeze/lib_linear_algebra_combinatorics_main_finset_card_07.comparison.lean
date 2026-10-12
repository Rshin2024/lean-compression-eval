

theorem _root_.CompressionEvalSignatureOriginal (h : a ∉ s) : #(insert a s) = #s + 1 := by
  grind [=_ cons_eq_insert]

theorem _root_.CompressionEvalSignatureMaterialized {«α» : Type u_1} {«s» : Finset.{u_1} α} {«a» : α} [«inst» : DecidableEq.{u_1 + 1} α] (h : a ∉ s) : #(insert a s) = #s + 1 := by
  grind [=_ cons_eq_insert]

run_cmd do
  let env ← Lean.getEnv
  let some old := env.find? `CompressionEvalSignatureOriginal | throwError "missing original comparison declaration"
  let some fresh := env.find? `CompressionEvalSignatureMaterialized | throwError "missing materialized comparison declaration"
  unless old.levelParams.length == fresh.levelParams.length do throwError "universe parameter count changed"
  let oldType := old.type.instantiateLevelParams old.levelParams (fresh.levelParams.map Lean.Level.param)
  unless Lean.Kernel.isDefEqGuarded env {} oldType fresh.type do throwError "materialized signature differs from original"
