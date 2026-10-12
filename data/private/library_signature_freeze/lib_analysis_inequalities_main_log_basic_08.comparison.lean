

theorem _root_.CompressionEvalSignatureOriginal {x : ℝ} : 2 * x ≤ exp x := by
  by_cases hx0 : x < 0
  · exact le_trans (mul_nonpos_of_nonneg_of_nonpos (by simp only [Nat.ofNat_nonneg]) hx0.le)
      (exp_nonneg x)
  · apply le_trans (mul_le_mul_of_nonneg_right _ (le_of_not_gt hx0)) exp_one_mul_le_exp
    have := Real.add_one_le_exp 1
    rwa [one_add_one_eq_two] at this

theorem _root_.CompressionEvalSignatureMaterialized {x : ℝ} : 2 * x ≤ exp x := by
  by_cases hx0 : x < 0
  · exact le_trans (mul_nonpos_of_nonneg_of_nonpos (by simp only [Nat.ofNat_nonneg]) hx0.le)
      (exp_nonneg x)
  · apply le_trans (mul_le_mul_of_nonneg_right _ (le_of_not_gt hx0)) exp_one_mul_le_exp
    have := Real.add_one_le_exp 1
    rwa [one_add_one_eq_two] at this

run_cmd do
  let env ← Lean.getEnv
  let some old := env.find? `CompressionEvalSignatureOriginal | throwError "missing original comparison declaration"
  let some fresh := env.find? `CompressionEvalSignatureMaterialized | throwError "missing materialized comparison declaration"
  unless old.levelParams.length == fresh.levelParams.length do throwError "universe parameter count changed"
  let oldType := old.type.instantiateLevelParams old.levelParams (fresh.levelParams.map Lean.Level.param)
  unless Lean.Kernel.isDefEqGuarded env {} oldType fresh.type do throwError "materialized signature differs from original"
