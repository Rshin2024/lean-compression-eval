

theorem _root_.CompressionEvalSignatureOriginal (hn : n ≠ 0) : Injective (fun a : ℕ ↦ a ^ n) := by
  simp [Injective, le_antisymm_iff, Nat.pow_le_pow_iff_left hn]

theorem _root_.CompressionEvalSignatureMaterialized {«n» : ℕ} (hn : n ≠ 0) : Injective (fun a : ℕ ↦ a ^ n) := by
  simp [Injective, le_antisymm_iff, Nat.pow_le_pow_iff_left hn]

run_cmd do
  let env ← Lean.getEnv
  let some old := env.find? `CompressionEvalSignatureOriginal | throwError "missing original comparison declaration"
  let some fresh := env.find? `CompressionEvalSignatureMaterialized | throwError "missing materialized comparison declaration"
  unless old.levelParams.length == fresh.levelParams.length do throwError "universe parameter count changed"
  let oldType := old.type.instantiateLevelParams old.levelParams (fresh.levelParams.map Lean.Level.param)
  unless Lean.Kernel.isDefEqGuarded env {} oldType fresh.type do throwError "materialized signature differs from original"
