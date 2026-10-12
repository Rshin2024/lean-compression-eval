

theorem _root_.CompressionEvalSignatureOriginal {p : ℕ} (pp : Prime p) : succ (pred p) = p := succ_pred_eq_of_pos pp.pos

theorem _root_.CompressionEvalSignatureMaterialized {p : ℕ} (pp : Prime p) : succ (pred p) = p := succ_pred_eq_of_pos pp.pos

run_cmd do
  let env ← Lean.getEnv
  let some old := env.find? `CompressionEvalSignatureOriginal | throwError "missing original comparison declaration"
  let some fresh := env.find? `CompressionEvalSignatureMaterialized | throwError "missing materialized comparison declaration"
  unless old.levelParams.length == fresh.levelParams.length do throwError "universe parameter count changed"
  let oldType := old.type.instantiateLevelParams old.levelParams (fresh.levelParams.map Lean.Level.param)
  unless Lean.Kernel.isDefEqGuarded env {} oldType fresh.type do throwError "materialized signature differs from original"
