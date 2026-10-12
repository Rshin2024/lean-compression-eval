

theorem _root_.CompressionEvalSignatureOriginal (f₁ f₂ : α → β) : EqOn f₁ f₂ univ ↔ f₁ = f₂ := by
  simp [EqOn, funext_iff]

theorem _root_.CompressionEvalSignatureMaterialized {«α» : Type u_1} {«β» : Type u_2} (f₁ f₂ : α → β) : EqOn f₁ f₂ univ ↔ f₁ = f₂ := by
  simp [EqOn, funext_iff]

run_cmd do
  let env ← Lean.getEnv
  let some old := env.find? `CompressionEvalSignatureOriginal | throwError "missing original comparison declaration"
  let some fresh := env.find? `CompressionEvalSignatureMaterialized | throwError "missing materialized comparison declaration"
  unless old.levelParams.length == fresh.levelParams.length do throwError "universe parameter count changed"
  let oldType := old.type.instantiateLevelParams old.levelParams (fresh.levelParams.map Lean.Level.param)
  unless Lean.Kernel.isDefEqGuarded env {} oldType fresh.type do throwError "materialized signature differs from original"
