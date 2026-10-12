

theorem _root_.CompressionEvalSignatureOriginal (x : ℝ) (n : ℕ) [n.AtLeastTwo] : x ^ (-ofNat(n) : ℝ) = x ^ (-ofNat(n) : ℤ) := rpow_neg_natCast _ _

theorem _root_.CompressionEvalSignatureMaterialized (x : ℝ) (n : ℕ) [n.AtLeastTwo] : x ^ (-ofNat(n) : ℝ) = x ^ (-ofNat(n) : ℤ) := rpow_neg_natCast _ _

run_cmd do
  let env ← Lean.getEnv
  let Option.some old := env.find? `CompressionEvalSignatureOriginal | throwError "missing original comparison declaration"
  let Option.some fresh := env.find? `CompressionEvalSignatureMaterialized | throwError "missing materialized comparison declaration"
  unless old.levelParams.length == fresh.levelParams.length do throwError "universe parameter count changed"
  let oldType := old.type.instantiateLevelParams old.levelParams (fresh.levelParams.map Lean.Level.param)
  unless Lean.Kernel.isDefEqGuarded env {} oldType fresh.type do throwError "materialized signature differs from original"
