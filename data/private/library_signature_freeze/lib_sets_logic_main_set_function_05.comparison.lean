

theorem _root_.CompressionEvalSignatureOriginal (h : EqOn f₁ f₂ s) : EqOn f₂ f₁ s := fun _ hx => (h hx).symm

theorem _root_.CompressionEvalSignatureMaterialized {«α» : Type u_1} {«β» : Type u_2} {«s» : Set.{u_1} α} {«f₁» : α → β} {«f₂» : α → β} (h : EqOn f₁ f₂ s) : EqOn f₂ f₁ s := fun _ hx => (h hx).symm

run_cmd do
  let env ← Lean.getEnv
  let some old := env.find? `CompressionEvalSignatureOriginal | throwError "missing original comparison declaration"
  let some fresh := env.find? `CompressionEvalSignatureMaterialized | throwError "missing materialized comparison declaration"
  unless old.levelParams.length == fresh.levelParams.length do throwError "universe parameter count changed"
  let oldType := old.type.instantiateLevelParams old.levelParams (fresh.levelParams.map Lean.Level.param)
  unless Lean.Kernel.isDefEqGuarded env {} oldType fresh.type do throwError "materialized signature differs from original"
