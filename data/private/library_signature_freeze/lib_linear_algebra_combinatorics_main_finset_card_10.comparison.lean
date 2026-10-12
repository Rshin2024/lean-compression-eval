

theorem _root_.CompressionEvalSignatureOriginal  : #{a, b} ≤ 2 := card_insert_le _ _

theorem _root_.CompressionEvalSignatureMaterialized {«α» : Type u_1} [«inst» : DecidableEq.{u_1 + 1} α] {«a» : α} {«b» : α} : #{a, b} ≤ 2 := card_insert_le _ _

run_cmd do
  let env ← Lean.getEnv
  let some old := env.find? `CompressionEvalSignatureOriginal | throwError "missing original comparison declaration"
  let some fresh := env.find? `CompressionEvalSignatureMaterialized | throwError "missing materialized comparison declaration"
  unless old.levelParams.length == fresh.levelParams.length do throwError "universe parameter count changed"
  let oldType := old.type.instantiateLevelParams old.levelParams (fresh.levelParams.map Lean.Level.param)
  unless Lean.Kernel.isDefEqGuarded env {} oldType fresh.type do throwError "materialized signature differs from original"
