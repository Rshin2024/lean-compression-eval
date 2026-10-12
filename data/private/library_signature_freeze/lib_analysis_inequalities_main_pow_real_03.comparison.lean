

theorem _root_.CompressionEvalSignatureOriginal {x : ℝ} (hx : 0 ≤ x) (y : ℝ) : x ^ y = if x = 0 then if y = 0 then 1 else 0 else exp (log x * y) := by
  simp only [rpow_def, Complex.cpow_def]; split_ifs <;>
  simp_all [(Complex.ofReal_log hx).symm, -Complex.ofReal_mul,
      (Complex.ofReal_mul _ _).symm, Complex.exp_ofReal_re, Complex.ofReal_eq_zero]

theorem _root_.CompressionEvalSignatureMaterialized {x : ℝ} (hx : 0 ≤ x) (y : ℝ) : x ^ y = if x = 0 then if y = 0 then 1 else 0 else exp (log x * y) := by
  simp only [rpow_def, Complex.cpow_def]; split_ifs <;>
  simp_all [(Complex.ofReal_log hx).symm, -Complex.ofReal_mul,
      (Complex.ofReal_mul _ _).symm, Complex.exp_ofReal_re, Complex.ofReal_eq_zero]

run_cmd do
  let env ← Lean.getEnv
  let Option.some old := env.find? `CompressionEvalSignatureOriginal | throwError "missing original comparison declaration"
  let Option.some fresh := env.find? `CompressionEvalSignatureMaterialized | throwError "missing materialized comparison declaration"
  unless old.levelParams.length == fresh.levelParams.length do throwError "universe parameter count changed"
  let oldType := old.type.instantiateLevelParams old.levelParams (fresh.levelParams.map Lean.Level.param)
  unless Lean.Kernel.isDefEqGuarded env {} oldType fresh.type do throwError "materialized signature differs from original"
