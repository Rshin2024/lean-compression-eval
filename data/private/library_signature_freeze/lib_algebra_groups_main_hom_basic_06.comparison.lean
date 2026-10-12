

theorem _root_.CompressionEvalSignatureOriginal [One M] [One N] [MulOneClass P] (g₁ g₂ : OneHom N P) (f : OneHom M N) : (g₁ * g₂).comp f = g₁.comp f * g₂.comp f := rfl

theorem _root_.CompressionEvalSignatureMaterialized {«M» : Type u_2} {«N» : Type u_3} {«P» : Type u_4} [One M] [One N] [MulOneClass P] (g₁ g₂ : OneHom N P) (f : OneHom M N) : (g₁ * g₂).comp f = g₁.comp f * g₂.comp f := rfl

run_cmd do
  let env ← Lean.getEnv
  let some old := env.find? `CompressionEvalSignatureOriginal | throwError "missing original comparison declaration"
  let some fresh := env.find? `CompressionEvalSignatureMaterialized | throwError "missing materialized comparison declaration"
  unless old.levelParams.length == fresh.levelParams.length do throwError "universe parameter count changed"
  let oldType := old.type.instantiateLevelParams old.levelParams (fresh.levelParams.map Lean.Level.param)
  unless Lean.Kernel.isDefEqGuarded env {} oldType fresh.type do throwError "materialized signature differs from original"
