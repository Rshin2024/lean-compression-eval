

theorem _root_.CompressionEvalSignatureOriginal (p : Prop) [Decidable p] (a b : α) (c : β) : (if p then a else b) ^ c = if p then a ^ c else b ^ c := dite_pow _ _ _ _

theorem _root_.CompressionEvalSignatureMaterialized {«α» : Type u_1} {«β» : Type u_2} [«inst» : Pow.{u_1, u_2} α β] (p : Prop) [Decidable p] (a b : α) (c : β) : (if p then a else b) ^ c = if p then a ^ c else b ^ c := dite_pow _ _ _ _

run_cmd do
  let env ← Lean.getEnv
  let some old := env.find? `CompressionEvalSignatureOriginal | throwError "missing original comparison declaration"
  let some fresh := env.find? `CompressionEvalSignatureMaterialized | throwError "missing materialized comparison declaration"
  unless old.levelParams.length == fresh.levelParams.length do throwError "universe parameter count changed"
  let oldType := old.type.instantiateLevelParams old.levelParams (fresh.levelParams.map Lean.Level.param)
  unless Lean.Kernel.isDefEqGuarded env {} oldType fresh.type do throwError "materialized signature differs from original"
