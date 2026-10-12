

theorem _root_.CompressionEvalSignatureOriginal (a b : α) : ‖a / b‖₊ = ‖a‖₊ / ‖b‖₊ := map_div₀ (nnnormHom : α →*₀ ℝ≥0) a b

theorem _root_.CompressionEvalSignatureMaterialized {«α» : Type u_2} [«inst» : NormedDivisionRing.{u_2} α] (a b : α) : ‖a / b‖₊ = ‖a‖₊ / ‖b‖₊ := map_div₀ (nnnormHom : α →*₀ ℝ≥0) a b

run_cmd do
  let env ← Lean.getEnv
  let some old := env.find? `CompressionEvalSignatureOriginal | throwError "missing original comparison declaration"
  let some fresh := env.find? `CompressionEvalSignatureMaterialized | throwError "missing materialized comparison declaration"
  unless old.levelParams.length == fresh.levelParams.length do throwError "universe parameter count changed"
  let oldType := old.type.instantiateLevelParams old.levelParams (fresh.levelParams.map Lean.Level.param)
  unless Lean.Kernel.isDefEqGuarded env {} oldType fresh.type do throwError "materialized signature differs from original"
