

theorem _root_.CompressionEvalSignatureOriginal (x : ℝ) : log (exp x) = x := exp_injective <| exp_log (exp_pos x)

theorem _root_.CompressionEvalSignatureMaterialized (x : ℝ) : log (exp x) = x := exp_injective <| exp_log (exp_pos x)

run_cmd do
  let env ← Lean.getEnv
  let some old := env.find? `CompressionEvalSignatureOriginal | throwError "missing original comparison declaration"
  let some fresh := env.find? `CompressionEvalSignatureMaterialized | throwError "missing materialized comparison declaration"
  unless old.levelParams.length == fresh.levelParams.length do throwError "universe parameter count changed"
  let oldType := old.type.instantiateLevelParams old.levelParams (fresh.levelParams.map Lean.Level.param)
  unless Lean.Kernel.isDefEqGuarded env {} oldType fresh.type do throwError "materialized signature differs from original"
