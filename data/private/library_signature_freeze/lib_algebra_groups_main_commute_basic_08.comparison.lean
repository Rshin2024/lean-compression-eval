

theorem _root_.CompressionEvalSignatureOriginal (hcd : Commute c d) (hbc : Commute b c⁻¹) : a * b / (c * d) = a / c * (b / d) := (hcd.div_mul_div_comm hbc.symm).symm

theorem _root_.CompressionEvalSignatureMaterialized {«G» : Type u_1} [«inst» : DivisionMonoid.{u_1} G] {«a» : G} {«b» : G} {«c» : G} {«d» : G} (hcd : Commute c d) (hbc : Commute b c⁻¹) : a * b / (c * d) = a / c * (b / d) := (hcd.div_mul_div_comm hbc.symm).symm

run_cmd do
  let env ← Lean.getEnv
  let some old := env.find? `CompressionEvalSignatureOriginal | throwError "missing original comparison declaration"
  let some fresh := env.find? `CompressionEvalSignatureMaterialized | throwError "missing materialized comparison declaration"
  unless old.levelParams.length == fresh.levelParams.length do throwError "universe parameter count changed"
  let oldType := old.type.instantiateLevelParams old.levelParams (fresh.levelParams.map Lean.Level.param)
  unless Lean.Kernel.isDefEqGuarded env {} oldType fresh.type do throwError "materialized signature differs from original"
