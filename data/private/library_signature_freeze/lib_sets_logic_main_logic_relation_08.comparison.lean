

theorem _root_.CompressionEvalSignatureOriginal (h : Equivalence r) (f : α → β) : Equivalence (r on f) := ⟨fun a ↦ h.refl (f a), h.symm, h.trans⟩

theorem _root_.CompressionEvalSignatureMaterialized {«α» : Type u_1} {«β» : Type u_2} {«r» : β → β → Prop} (h : Equivalence r) (f : α → β) : Equivalence (r on f) := ⟨fun a ↦ h.refl (f a), h.symm, h.trans⟩

run_cmd do
  let env ← Lean.getEnv
  let Option.some old := env.find? `CompressionEvalSignatureOriginal | throwError "missing original comparison declaration"
  let Option.some fresh := env.find? `CompressionEvalSignatureMaterialized | throwError "missing materialized comparison declaration"
  unless old.levelParams.length == fresh.levelParams.length do throwError "universe parameter count changed"
  let oldType := old.type.instantiateLevelParams old.levelParams (fresh.levelParams.map Lean.Level.param)
  unless Lean.Kernel.isDefEqGuarded env {} oldType fresh.type do throwError "materialized signature differs from original"
