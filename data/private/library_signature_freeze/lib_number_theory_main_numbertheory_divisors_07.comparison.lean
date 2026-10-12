

theorem _root_.CompressionEvalSignatureOriginal {m : ℕ} : n ∈ divisors m ↔ n ∣ m ∧ m ≠ 0 := by
  rcases eq_or_ne m 0 with (rfl | hm); · simp [divisors]
  simp only [hm, Ne, not_false_iff, and_true, ← filter_dvd_eq_divisors hm, mem_filter,
    mem_range, and_iff_right_iff_imp, Nat.lt_succ_iff]
  exact le_of_dvd hm.bot_lt

theorem _root_.CompressionEvalSignatureMaterialized {«n» : ℕ} {m : ℕ} : n ∈ divisors m ↔ n ∣ m ∧ m ≠ 0 := by
  rcases eq_or_ne m 0 with (rfl | hm); · simp [divisors]
  simp only [hm, Ne, not_false_iff, and_true, ← filter_dvd_eq_divisors hm, mem_filter,
    mem_range, and_iff_right_iff_imp, Nat.lt_succ_iff]
  exact le_of_dvd hm.bot_lt

run_cmd do
  let env ← Lean.getEnv
  let some old := env.find? `CompressionEvalSignatureOriginal | throwError "missing original comparison declaration"
  let some fresh := env.find? `CompressionEvalSignatureMaterialized | throwError "missing materialized comparison declaration"
  unless old.levelParams.length == fresh.levelParams.length do throwError "universe parameter count changed"
  let oldType := old.type.instantiateLevelParams old.levelParams (fresh.levelParams.map Lean.Level.param)
  unless Lean.Kernel.isDefEqGuarded env {} oldType fresh.type do throwError "materialized signature differs from original"
