

theorem _root_.CompressionEvalSignatureOriginal (x y : α) : (· * x) ∘ (· * y) = (· * (y * x)) := by
  ext z
  simp [mul_assoc]

theorem _root_.CompressionEvalSignatureMaterialized {«α» : Type u_1} [«inst» : Semigroup.{u_1} α] (x y : α) : (· * x) ∘ (· * y) = (· * (y * x)) := by
  ext z
  simp [mul_assoc]

run_cmd do
  let env ← Lean.getEnv
  let some old := env.find? `CompressionEvalSignatureOriginal | throwError "missing original comparison declaration"
  let some fresh := env.find? `CompressionEvalSignatureMaterialized | throwError "missing materialized comparison declaration"
  unless old.levelParams.length == fresh.levelParams.length do throwError "universe parameter count changed"
  let oldType := old.type.instantiateLevelParams old.levelParams (fresh.levelParams.map Lean.Level.param)
  unless Lean.Kernel.isDefEqGuarded env {} oldType fresh.type do throwError "materialized signature differs from original"
