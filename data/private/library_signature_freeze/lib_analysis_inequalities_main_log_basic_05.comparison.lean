

theorem _root_.CompressionEvalSignatureOriginal (x : ℝ) : x ≤ exp (log x) := by
  by_cases h_zero : x = 0
  · rw [h_zero, log, dite_eq_left rfl, exp_zero]
    exact zero_le_one
  · rw [exp_log_eq_abs h_zero]
    exact le_abs_self _

theorem _root_.CompressionEvalSignatureMaterialized (x : ℝ) : x ≤ exp (log x) := by
  by_cases h_zero : x = 0
  · rw [h_zero, log, dite_eq_left rfl, exp_zero]
    exact zero_le_one
  · rw [exp_log_eq_abs h_zero]
    exact le_abs_self _

run_cmd do
  let env ← Lean.getEnv
  let some old := env.find? `CompressionEvalSignatureOriginal | throwError "missing original comparison declaration"
  let some fresh := env.find? `CompressionEvalSignatureMaterialized | throwError "missing materialized comparison declaration"
  unless old.levelParams.length == fresh.levelParams.length do throwError "universe parameter count changed"
  let oldType := old.type.instantiateLevelParams old.levelParams (fresh.levelParams.map Lean.Level.param)
  unless Lean.Kernel.isDefEqGuarded env {} oldType fresh.type do throwError "materialized signature differs from original"
