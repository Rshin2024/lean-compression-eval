

theorem _root_.CompressionEvalSignatureOriginal {p : ℕ} (hp : Prime p) : p = 2 ∨ Odd p := Or.imp_right (fun h => ⟨p / 2, (div_add_mod p 2).symm.trans (congr_arg _ h)⟩) hp.eq_two_or_odd

theorem _root_.CompressionEvalSignatureMaterialized {p : ℕ} (hp : Prime p) : p = 2 ∨ Odd p := Or.imp_right (fun h => ⟨p / 2, (div_add_mod p 2).symm.trans (congr_arg _ h)⟩) hp.eq_two_or_odd

run_cmd do
  let env ← Lean.getEnv
  let some old := env.find? `CompressionEvalSignatureOriginal | throwError "missing original comparison declaration"
  let some fresh := env.find? `CompressionEvalSignatureMaterialized | throwError "missing materialized comparison declaration"
  unless old.levelParams.length == fresh.levelParams.length do throwError "universe parameter count changed"
  let oldType := old.type.instantiateLevelParams old.levelParams (fresh.levelParams.map Lean.Level.param)
  unless Lean.Kernel.isDefEqGuarded env {} oldType fresh.type do throwError "materialized signature differs from original"
