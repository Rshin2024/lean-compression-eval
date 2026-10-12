

theorem _root_.CompressionEvalSignatureOriginal {n : ℕ} (n2 : 2 ≤ n) (np : ¬Prime n) : ∃ m, m ∣ n ∧ m ≠ 1 ∧ m ≠ n := ⟨minFac n, minFac_dvd _, ne_of_gt (minFac_prime (ne_of_gt n2)).one_lt,
    ne_of_lt <| (not_prime_iff_minFac_lt n2).1 np⟩

theorem _root_.CompressionEvalSignatureMaterialized {n : ℕ} (n2 : 2 ≤ n) (np : ¬Prime n) : ∃ m, m ∣ n ∧ m ≠ 1 ∧ m ≠ n := ⟨minFac n, minFac_dvd _, ne_of_gt (minFac_prime (ne_of_gt n2)).one_lt,
    ne_of_lt <| (not_prime_iff_minFac_lt n2).1 np⟩

run_cmd do
  let env ← Lean.getEnv
  let some old := env.find? `CompressionEvalSignatureOriginal | throwError "missing original comparison declaration"
  let some fresh := env.find? `CompressionEvalSignatureMaterialized | throwError "missing materialized comparison declaration"
  unless old.levelParams.length == fresh.levelParams.length do throwError "universe parameter count changed"
  let oldType := old.type.instantiateLevelParams old.levelParams (fresh.levelParams.map Lean.Level.param)
  unless Lean.Kernel.isDefEqGuarded env {} oldType fresh.type do throwError "materialized signature differs from original"
