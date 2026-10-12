

theorem _root_.CompressionEvalSignatureOriginal {a b d : ℕ} (hda : d ∣ a) (hdb : d ∣ b) (hd : ∀ e : ℕ, e ∣ a → e ∣ b → e ∣ d) : d = a.gcd b := (dvd_antisymm (hd _ (gcd_dvd_left a b) (gcd_dvd_right a b)) (dvd_gcd hda hdb)).symm

theorem _root_.CompressionEvalSignatureMaterialized {a b d : ℕ} (hda : d ∣ a) (hdb : d ∣ b) (hd : ∀ e : ℕ, e ∣ a → e ∣ b → e ∣ d) : d = a.gcd b := (dvd_antisymm (hd _ (gcd_dvd_left a b) (gcd_dvd_right a b)) (dvd_gcd hda hdb)).symm

run_cmd do
  let env ← Lean.getEnv
  let some old := env.find? `CompressionEvalSignatureOriginal | throwError "missing original comparison declaration"
  let some fresh := env.find? `CompressionEvalSignatureMaterialized | throwError "missing materialized comparison declaration"
  unless old.levelParams.length == fresh.levelParams.length do throwError "universe parameter count changed"
  let oldType := old.type.instantiateLevelParams old.levelParams (fresh.levelParams.map Lean.Level.param)
  unless Lean.Kernel.isDefEqGuarded env {} oldType fresh.type do throwError "materialized signature differs from original"
