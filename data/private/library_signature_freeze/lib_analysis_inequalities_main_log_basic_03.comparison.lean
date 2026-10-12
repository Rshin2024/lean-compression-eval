

theorem _root_.CompressionEvalSignatureOriginal (hx : 0 < x) : exp (log x) = x := by
  rw [exp_log_eq_abs hx.ne']
  exact abs_of_pos hx

theorem _root_.CompressionEvalSignatureMaterialized {«x» : ℝ} (hx : 0 < x) : exp (log x) = x := by
  rw [exp_log_eq_abs hx.ne']
  exact abs_of_pos hx

run_cmd do
  let env ← Lean.getEnv
  let some old := env.find? `CompressionEvalSignatureOriginal | throwError "missing original comparison declaration"
  let some fresh := env.find? `CompressionEvalSignatureMaterialized | throwError "missing materialized comparison declaration"
  unless old.levelParams.length == fresh.levelParams.length do throwError "universe parameter count changed"
  let oldType := old.type.instantiateLevelParams old.levelParams (fresh.levelParams.map Lean.Level.param)
  unless Lean.Kernel.isDefEqGuarded env {} oldType fresh.type do throwError "materialized signature differs from original"
