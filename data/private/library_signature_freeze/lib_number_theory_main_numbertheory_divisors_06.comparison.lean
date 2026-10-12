

theorem _root_.CompressionEvalSignatureOriginal (h : n ≠ 0) : cons n (properDivisors n) self_notMem_properDivisors = divisors n := by
  rw [cons_eq_insert, insert_self_properDivisors h]

theorem _root_.CompressionEvalSignatureMaterialized {«n» : ℕ} (h : n ≠ 0) : cons n (properDivisors n) self_notMem_properDivisors = divisors n := by
  rw [cons_eq_insert, insert_self_properDivisors h]

run_cmd do
  let env ← Lean.getEnv
  let some old := env.find? `CompressionEvalSignatureOriginal | throwError "missing original comparison declaration"
  let some fresh := env.find? `CompressionEvalSignatureMaterialized | throwError "missing materialized comparison declaration"
  unless old.levelParams.length == fresh.levelParams.length do throwError "universe parameter count changed"
  let oldType := old.type.instantiateLevelParams old.levelParams (fresh.levelParams.map Lean.Level.param)
  unless Lean.Kernel.isDefEqGuarded env {} oldType fresh.type do throwError "materialized signature differs from original"
