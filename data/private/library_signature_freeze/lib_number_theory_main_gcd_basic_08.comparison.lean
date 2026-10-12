

theorem _root_.CompressionEvalSignatureOriginal {m n k : ℕ} (H : Coprime k n) : k ∣ m * n ↔ k ∣ m := ⟨H.dvd_of_dvd_mul_right, fun h => dvd_mul_of_dvd_left h n⟩

theorem _root_.CompressionEvalSignatureMaterialized {m n k : ℕ} (H : Coprime k n) : k ∣ m * n ↔ k ∣ m := ⟨H.dvd_of_dvd_mul_right, fun h => dvd_mul_of_dvd_left h n⟩

run_cmd do
  let env ← Lean.getEnv
  let some old := env.find? `CompressionEvalSignatureOriginal | throwError "missing original comparison declaration"
  let some fresh := env.find? `CompressionEvalSignatureMaterialized | throwError "missing materialized comparison declaration"
  unless old.levelParams.length == fresh.levelParams.length do throwError "universe parameter count changed"
  let oldType := old.type.instantiateLevelParams old.levelParams (fresh.levelParams.map Lean.Level.param)
  unless Lean.Kernel.isDefEqGuarded env {} oldType fresh.type do throwError "materialized signature differs from original"
