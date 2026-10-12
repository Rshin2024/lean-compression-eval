

theorem _root_.CompressionEvalSignatureOriginal (hbd : Commute b d) (hbc : Commute b⁻¹ c) : a / b * (c / d) = a * c / (b * d) := by
  simp_rw [div_eq_mul_inv, mul_inv_rev, hbd.inv_inv.symm.eq, hbc.mul_mul_mul_comm]

theorem _root_.CompressionEvalSignatureMaterialized {«G» : Type u_1} [«inst» : DivisionMonoid.{u_1} G] {«a» : G} {«b» : G} {«c» : G} {«d» : G} (hbd : Commute b d) (hbc : Commute b⁻¹ c) : a / b * (c / d) = a * c / (b * d) := by
  simp_rw [div_eq_mul_inv, mul_inv_rev, hbd.inv_inv.symm.eq, hbc.mul_mul_mul_comm]

run_cmd do
  let env ← Lean.getEnv
  let some old := env.find? `CompressionEvalSignatureOriginal | throwError "missing original comparison declaration"
  let some fresh := env.find? `CompressionEvalSignatureMaterialized | throwError "missing materialized comparison declaration"
  unless old.levelParams.length == fresh.levelParams.length do throwError "universe parameter count changed"
  let oldType := old.type.instantiateLevelParams old.levelParams (fresh.levelParams.map Lean.Level.param)
  unless Lean.Kernel.isDefEqGuarded env {} oldType fresh.type do throwError "materialized signature differs from original"
