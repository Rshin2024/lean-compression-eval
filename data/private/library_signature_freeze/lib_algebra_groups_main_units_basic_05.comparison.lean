

theorem _root_.CompressionEvalSignatureOriginal (a : αˣ) {b c : α} : b * a = c * a ↔ b = c := ⟨fun h => by simpa only [mul_inv_cancel_right] using congr_arg (fun x : α => x * ↑(a⁻¹ : αˣ)) h,
    congr_arg (· * a.val)⟩

theorem _root_.CompressionEvalSignatureMaterialized {«α» : Type u} [«inst» : Monoid.{u} α] (a : αˣ) {b c : α} : b * a = c * a ↔ b = c := ⟨fun h => by simpa only [mul_inv_cancel_right] using congr_arg (fun x : α => x * ↑(a⁻¹ : αˣ)) h,
    congr_arg (· * a.val)⟩

run_cmd do
  let env ← Lean.getEnv
  let some old := env.find? `CompressionEvalSignatureOriginal | throwError "missing original comparison declaration"
  let some fresh := env.find? `CompressionEvalSignatureMaterialized | throwError "missing materialized comparison declaration"
  unless old.levelParams.length == fresh.levelParams.length do throwError "universe parameter count changed"
  let oldType := old.type.instantiateLevelParams old.levelParams (fresh.levelParams.map Lean.Level.param)
  unless Lean.Kernel.isDefEqGuarded env {} oldType fresh.type do throwError "materialized signature differs from original"
