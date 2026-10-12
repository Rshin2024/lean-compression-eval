

theorem _root_.CompressionEvalSignatureOriginal {M N} [One M] [DivisionMonoid N] (f g : OneHom M N) : ⇑(f / g) = ⇑f / ⇑g := rfl

theorem _root_.CompressionEvalSignatureMaterialized {M N} [One M] [DivisionMonoid N] (f g : OneHom M N) : ⇑(f / g) = ⇑f / ⇑g := rfl

run_cmd do
  let env ← Lean.getEnv
  let some old := env.find? `CompressionEvalSignatureOriginal | throwError "missing original comparison declaration"
  let some fresh := env.find? `CompressionEvalSignatureMaterialized | throwError "missing materialized comparison declaration"
  unless old.levelParams.length == fresh.levelParams.length do throwError "universe parameter count changed"
  let oldType := old.type.instantiateLevelParams old.levelParams (fresh.levelParams.map Lean.Level.param)
  unless Lean.Kernel.isDefEqGuarded env {} oldType fresh.type do throwError "materialized signature differs from original"
