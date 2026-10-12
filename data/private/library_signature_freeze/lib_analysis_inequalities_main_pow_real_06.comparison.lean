

theorem _root_.CompressionEvalSignatureOriginal (x : ℝ) (n : ℤ) : x ^ (n : ℝ) = x ^ n := by
  simp only [rpow_def, ← Complex.ofReal_zpow, Complex.cpow_intCast, Complex.ofReal_intCast,
    Complex.ofReal_re]

theorem _root_.CompressionEvalSignatureMaterialized (x : ℝ) (n : ℤ) : x ^ (n : ℝ) = x ^ n := by
  simp only [rpow_def, ← Complex.ofReal_zpow, Complex.cpow_intCast, Complex.ofReal_intCast,
    Complex.ofReal_re]

run_cmd do
  let env ← Lean.getEnv
  let Option.some old := env.find? `CompressionEvalSignatureOriginal | throwError "missing original comparison declaration"
  let Option.some fresh := env.find? `CompressionEvalSignatureMaterialized | throwError "missing materialized comparison declaration"
  unless old.levelParams.length == fresh.levelParams.length do throwError "universe parameter count changed"
  let oldType := old.type.instantiateLevelParams old.levelParams (fresh.levelParams.map Lean.Level.param)
  unless Lean.Kernel.isDefEqGuarded env {} oldType fresh.type do throwError "materialized signature differs from original"
