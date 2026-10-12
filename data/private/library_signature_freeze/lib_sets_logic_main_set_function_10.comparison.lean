

theorem _root_.CompressionEvalSignatureOriginal {f : α → α} (h : Set.EqOn f id s) : f '' s = s := by grind

theorem _root_.CompressionEvalSignatureMaterialized {«α» : Type u_1} {«s» : Set.{u_1} α} {f : α → α} (h : Set.EqOn f id s) : f '' s = s := by grind

run_cmd do
  let env ← Lean.getEnv
  let some old := env.find? `CompressionEvalSignatureOriginal | throwError "missing original comparison declaration"
  let some fresh := env.find? `CompressionEvalSignatureMaterialized | throwError "missing materialized comparison declaration"
  unless old.levelParams.length == fresh.levelParams.length do throwError "universe parameter count changed"
  let oldType := old.type.instantiateLevelParams old.levelParams (fresh.levelParams.map Lean.Level.param)
  unless Lean.Kernel.isDefEqGuarded env {} oldType fresh.type do throwError "materialized signature differs from original"
