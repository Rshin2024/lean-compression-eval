

theorem _root_.CompressionEvalSignatureOriginal {x a : ℕ} (hx : x ≠ 0) (ha : a ≠ 0) : x ^ (a - 1) = x ^ a / x := by
  rw [← Nat.pow_div (one_le_iff_ne_zero.mpr ha) (Nat.pos_iff_ne_zero.mpr hx), Nat.pow_one]

theorem _root_.CompressionEvalSignatureMaterialized {x a : ℕ} (hx : x ≠ 0) (ha : a ≠ 0) : x ^ (a - 1) = x ^ a / x := by
  rw [← Nat.pow_div (one_le_iff_ne_zero.mpr ha) (Nat.pos_iff_ne_zero.mpr hx), Nat.pow_one]

run_cmd do
  let env ← Lean.getEnv
  let some old := env.find? `CompressionEvalSignatureOriginal | throwError "missing original comparison declaration"
  let some fresh := env.find? `CompressionEvalSignatureMaterialized | throwError "missing materialized comparison declaration"
  unless old.levelParams.length == fresh.levelParams.length do throwError "universe parameter count changed"
  let oldType := old.type.instantiateLevelParams old.levelParams (fresh.levelParams.map Lean.Level.param)
  unless Lean.Kernel.isDefEqGuarded env {} oldType fresh.type do throwError "materialized signature differs from original"
