

theorem _root_.CompressionEvalSignatureOriginal (h : n ≠ 0) : insert n (properDivisors n) = divisors n := by
  rw [divisors, properDivisors,
    ← Finset.insert_Ico_right_eq_Ico_add_one (one_le_iff_ne_zero.2 h),
    Finset.filter_insert, ite_eq_left (dvd_refl n)]

theorem _root_.CompressionEvalSignatureMaterialized {«n» : ℕ} (h : n ≠ 0) : insert n (properDivisors n) = divisors n := by
  rw [divisors, properDivisors,
    ← Finset.insert_Ico_right_eq_Ico_add_one (one_le_iff_ne_zero.2 h),
    Finset.filter_insert, ite_eq_left (dvd_refl n)]

run_cmd do
  let env ← Lean.getEnv
  let some old := env.find? `CompressionEvalSignatureOriginal | throwError "missing original comparison declaration"
  let some fresh := env.find? `CompressionEvalSignatureMaterialized | throwError "missing materialized comparison declaration"
  unless old.levelParams.length == fresh.levelParams.length do throwError "universe parameter count changed"
  let oldType := old.type.instantiateLevelParams old.levelParams (fresh.levelParams.map Lean.Level.param)
  unless Lean.Kernel.isDefEqGuarded env {} oldType fresh.type do throwError "materialized signature differs from original"
