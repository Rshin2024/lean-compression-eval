

theorem _root_.CompressionEvalSignatureOriginal (hbc : Commute b c) (hbd : Commute b⁻¹ d) (hcd : Commute c⁻¹ d) : a / b / (c / d) = a / c / (b / d) := by
  simp_rw [div_eq_mul_inv, mul_inv_rev, inv_inv, hbd.symm.eq, hcd.symm.eq,
    hbc.inv_inv.mul_mul_mul_comm]

theorem _root_.CompressionEvalSignatureMaterialized {«G» : Type u_1} [«inst» : DivisionMonoid.{u_1} G] {«a» : G} {«b» : G} {«c» : G} {«d» : G} (hbc : Commute b c) (hbd : Commute b⁻¹ d) (hcd : Commute c⁻¹ d) : a / b / (c / d) = a / c / (b / d) := by
  simp_rw [div_eq_mul_inv, mul_inv_rev, inv_inv, hbd.symm.eq, hcd.symm.eq,
    hbc.inv_inv.mul_mul_mul_comm]

run_cmd do
  let env ← Lean.getEnv
  let some old := env.find? `CompressionEvalSignatureOriginal | throwError "missing original comparison declaration"
  let some fresh := env.find? `CompressionEvalSignatureMaterialized | throwError "missing materialized comparison declaration"
  unless old.levelParams.length == fresh.levelParams.length do throwError "universe parameter count changed"
  let oldType := old.type.instantiateLevelParams old.levelParams (fresh.levelParams.map Lean.Level.param)
  unless Lean.Kernel.isDefEqGuarded env {} oldType fresh.type do throwError "materialized signature differs from original"
