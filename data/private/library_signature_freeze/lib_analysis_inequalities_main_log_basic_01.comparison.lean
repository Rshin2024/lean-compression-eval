

theorem _root_.CompressionEvalSignatureOriginal (hx : x ≠ 0) : log x = expOrderIso.symm ⟨|x|, abs_pos.2 hx⟩ := dite_eq_right hx

theorem _root_.CompressionEvalSignatureMaterialized {«x» : ℝ} (hx : x ≠ 0) : log x = expOrderIso.symm ⟨|x|, abs_pos.2 hx⟩ := dite_eq_right hx

run_cmd do
  let env ← Lean.getEnv
  let some old := env.find? `CompressionEvalSignatureOriginal | throwError "missing original comparison declaration"
  let some fresh := env.find? `CompressionEvalSignatureMaterialized | throwError "missing materialized comparison declaration"
  unless old.levelParams.length == fresh.levelParams.length do throwError "universe parameter count changed"
  let oldType := old.type.instantiateLevelParams old.levelParams (fresh.levelParams.map Lean.Level.param)
  unless Lean.Kernel.isDefEqGuarded env {} oldType fresh.type do throwError "materialized signature differs from original"
