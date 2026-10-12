

theorem _root_.CompressionEvalSignatureOriginal {m n : ℕ} : Coprime m (n + m) ↔ Coprime m n := by
  rw [Coprime, Coprime, gcd_add_self_right]

theorem _root_.CompressionEvalSignatureMaterialized {m n : ℕ} : Coprime m (n + m) ↔ Coprime m n := by
  rw [Coprime, Coprime, gcd_add_self_right]

run_cmd do
  let env ← Lean.getEnv
  let some old := env.find? `CompressionEvalSignatureOriginal | throwError "missing original comparison declaration"
  let some fresh := env.find? `CompressionEvalSignatureMaterialized | throwError "missing materialized comparison declaration"
  unless old.levelParams.length == fresh.levelParams.length do throwError "universe parameter count changed"
  let oldType := old.type.instantiateLevelParams old.levelParams (fresh.levelParams.map Lean.Level.param)
  unless Lean.Kernel.isDefEqGuarded env {} oldType fresh.type do throwError "materialized signature differs from original"
