

theorem _root_.CompressionEvalSignatureOriginal {P : Prop} [Decidable P] {a b : M} : ite P (a * b) 1 = ite P a 1 * ite P b 1 := by
  by_cases h : P <;> simp [h]

theorem _root_.CompressionEvalSignatureMaterialized {«M» : Type u_4} [«inst» : MulOneClass.{u_4} M] {P : Prop} [Decidable P] {a b : M} : ite P (a * b) 1 = ite P a 1 * ite P b 1 := by
  by_cases h : P <;> simp [h]

run_cmd do
  let env ← Lean.getEnv
  let some old := env.find? `CompressionEvalSignatureOriginal | throwError "missing original comparison declaration"
  let some fresh := env.find? `CompressionEvalSignatureMaterialized | throwError "missing materialized comparison declaration"
  unless old.levelParams.length == fresh.levelParams.length do throwError "universe parameter count changed"
  let oldType := old.type.instantiateLevelParams old.levelParams (fresh.levelParams.map Lean.Level.param)
  unless Lean.Kernel.isDefEqGuarded env {} oldType fresh.type do throwError "materialized signature differs from original"
