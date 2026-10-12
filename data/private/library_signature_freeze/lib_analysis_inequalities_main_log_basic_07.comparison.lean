

theorem _root_.CompressionEvalSignatureOriginal {x : ℝ} : exp 1 * x ≤ exp x := by
  by_cases hx0 : x ≤ 0
  · apply le_trans (mul_nonpos_of_nonneg_of_nonpos (exp_pos 1).le hx0) (exp_nonneg x)
  · have h := add_one_le_exp (log x)
    rwa [← exp_le_exp, exp_add, exp_log (lt_of_not_ge hx0), mul_comm] at h

theorem _root_.CompressionEvalSignatureMaterialized {x : ℝ} : exp 1 * x ≤ exp x := by
  by_cases hx0 : x ≤ 0
  · apply le_trans (mul_nonpos_of_nonneg_of_nonpos (exp_pos 1).le hx0) (exp_nonneg x)
  · have h := add_one_le_exp (log x)
    rwa [← exp_le_exp, exp_add, exp_log (lt_of_not_ge hx0), mul_comm] at h

run_cmd do
  let env ← Lean.getEnv
  let some old := env.find? `CompressionEvalSignatureOriginal | throwError "missing original comparison declaration"
  let some fresh := env.find? `CompressionEvalSignatureMaterialized | throwError "missing materialized comparison declaration"
  unless old.levelParams.length == fresh.levelParams.length do throwError "universe parameter count changed"
  let oldType := old.type.instantiateLevelParams old.levelParams (fresh.levelParams.map Lean.Level.param)
  unless Lean.Kernel.isDefEqGuarded env {} oldType fresh.type do throwError "materialized signature differs from original"
