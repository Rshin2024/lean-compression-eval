

theorem _root_.CompressionEvalSignatureOriginal {m : ℕ} : n ∈ properDivisors m ↔ n ∣ m ∧ n < m := by
  rcases eq_or_ne m 0 with (rfl | hm); · simp [properDivisors]
  simp only [and_comm, ← filter_dvd_eq_properDivisors hm, mem_filter, mem_range]

theorem _root_.CompressionEvalSignatureMaterialized {«n» : ℕ} {m : ℕ} : n ∈ properDivisors m ↔ n ∣ m ∧ n < m := by
  rcases eq_or_ne m 0 with (rfl | hm); · simp [properDivisors]
  simp only [and_comm, ← filter_dvd_eq_properDivisors hm, mem_filter, mem_range]

run_cmd do
  let env ← Lean.getEnv
  let some old := env.find? `CompressionEvalSignatureOriginal | throwError "missing original comparison declaration"
  let some fresh := env.find? `CompressionEvalSignatureMaterialized | throwError "missing materialized comparison declaration"
  unless old.levelParams.length == fresh.levelParams.length do throwError "universe parameter count changed"
  let oldType := old.type.instantiateLevelParams old.levelParams (fresh.levelParams.map Lean.Level.param)
  unless Lean.Kernel.isDefEqGuarded env {} oldType fresh.type do throwError "materialized signature differs from original"
