

theorem _root_.CompressionEvalSignatureOriginal {m : ℕ} (h : n ∈ divisors m) : m ≠ 0 := (mem_divisors.mp h).2

theorem _root_.CompressionEvalSignatureMaterialized {«n» : ℕ} {m : ℕ} (h : n ∈ divisors m) : m ≠ 0 := (mem_divisors.mp h).2

run_cmd do
  let env ← Lean.getEnv
  let some old := env.find? `CompressionEvalSignatureOriginal | throwError "missing original comparison declaration"
  let some fresh := env.find? `CompressionEvalSignatureMaterialized | throwError "missing materialized comparison declaration"
  unless old.levelParams.length == fresh.levelParams.length do throwError "universe parameter count changed"
  let oldType := old.type.instantiateLevelParams old.levelParams (fresh.levelParams.map Lean.Level.param)
  unless Lean.Kernel.isDefEqGuarded env {} oldType fresh.type do throwError "materialized signature differs from original"
