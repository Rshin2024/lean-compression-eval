

theorem _root_.CompressionEvalSignatureOriginal {a : α} (h : ↑u * a = 1) : ↑u⁻¹ = a := calc
    ↑u⁻¹ = ↑u⁻¹ * 1 := by rw [mul_one]
    _ = a := by rw [← h, inv_mul_cancel_left]

theorem _root_.CompressionEvalSignatureMaterialized {«α» : Type u} [«inst» : Monoid.{u} α] {«u» : Units.{u} α} {a : α} (h : ↑u * a = 1) : ↑u⁻¹ = a := calc
    ↑u⁻¹ = ↑u⁻¹ * 1 := by rw [mul_one]
    _ = a := by rw [← h, inv_mul_cancel_left]

run_cmd do
  let env ← Lean.getEnv
  let some old := env.find? `CompressionEvalSignatureOriginal | throwError "missing original comparison declaration"
  let some fresh := env.find? `CompressionEvalSignatureMaterialized | throwError "missing materialized comparison declaration"
  unless old.levelParams.length == fresh.levelParams.length do throwError "universe parameter count changed"
  let oldType := old.type.instantiateLevelParams old.levelParams (fresh.levelParams.map Lean.Level.param)
  unless Lean.Kernel.isDefEqGuarded env {} oldType fresh.type do throwError "materialized signature differs from original"
