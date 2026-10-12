

theorem _root_.CompressionEvalSignatureOriginal {m n k : ℕ} (H : Coprime k m) : k ∣ m * n ↔ k ∣ n := ⟨H.dvd_of_dvd_mul_left, fun h => dvd_mul_of_dvd_right h m⟩

theorem _root_.CompressionEvalSignatureMaterialized {m n k : ℕ} (H : Coprime k m) : k ∣ m * n ↔ k ∣ n := ⟨H.dvd_of_dvd_mul_left, fun h => dvd_mul_of_dvd_right h m⟩

run_cmd do
  let env ← Lean.getEnv
  let some old := env.find? `CompressionEvalSignatureOriginal | throwError "missing original comparison declaration"
  let some fresh := env.find? `CompressionEvalSignatureMaterialized | throwError "missing materialized comparison declaration"
  unless old.levelParams.length == fresh.levelParams.length do throwError "universe parameter count changed"
  let oldType := old.type.instantiateLevelParams old.levelParams (fresh.levelParams.map Lean.Level.param)
  unless Lean.Kernel.isDefEqGuarded env {} oldType fresh.type do throwError "materialized signature differs from original"
