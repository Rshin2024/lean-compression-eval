

theorem _root_.CompressionEvalSignatureOriginal (x y : ℝ) : x ^ y = ((x : ℂ) ^ (y : ℂ)).re := rfl

theorem _root_.CompressionEvalSignatureMaterialized (x y : ℝ) : x ^ y = ((x : ℂ) ^ (y : ℂ)).re := rfl

run_cmd do
  let env ← Lean.getEnv
  let Option.some old := env.find? `CompressionEvalSignatureOriginal | throwError "missing original comparison declaration"
  let Option.some fresh := env.find? `CompressionEvalSignatureMaterialized | throwError "missing materialized comparison declaration"
  unless old.levelParams.length == fresh.levelParams.length do throwError "universe parameter count changed"
  let oldType := old.type.instantiateLevelParams old.levelParams (fresh.levelParams.map Lean.Level.param)
  unless Lean.Kernel.isDefEqGuarded env {} oldType fresh.type do throwError "materialized signature differs from original"
