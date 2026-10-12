

theorem _root_.CompressionEvalSignatureOriginal {p : ℕ} (hp : Prime p) : p = 2 ∨ p % 2 = 1 := p.mod_two_eq_zero_or_one.imp_left fun h =>
    ((hp.eq_one_or_self_of_dvd 2 (dvd_of_mod_eq_zero h)).resolve_left (by decide)).symm

theorem _root_.CompressionEvalSignatureMaterialized {p : ℕ} (hp : Prime p) : p = 2 ∨ p % 2 = 1 := p.mod_two_eq_zero_or_one.imp_left fun h =>
    ((hp.eq_one_or_self_of_dvd 2 (dvd_of_mod_eq_zero h)).resolve_left (by decide)).symm

run_cmd do
  let env ← Lean.getEnv
  let some old := env.find? `CompressionEvalSignatureOriginal | throwError "missing original comparison declaration"
  let some fresh := env.find? `CompressionEvalSignatureMaterialized | throwError "missing materialized comparison declaration"
  unless old.levelParams.length == fresh.levelParams.length do throwError "universe parameter count changed"
  let oldType := old.type.instantiateLevelParams old.levelParams (fresh.levelParams.map Lean.Level.param)
  unless Lean.Kernel.isDefEqGuarded env {} oldType fresh.type do throwError "materialized signature differs from original"
