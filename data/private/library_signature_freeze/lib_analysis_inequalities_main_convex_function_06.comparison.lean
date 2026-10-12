

theorem _root_.CompressionEvalSignatureOriginal {s : Set β} (hs : Convex 𝕜 s) : ConcaveOn 𝕜 s _root_.id := ⟨hs, by
    intros
    rfl⟩

theorem _root_.CompressionEvalSignatureMaterialized {«𝕜» : Type u_1} {«β» : Type u_5} [«inst» : Semiring.{u_1} 𝕜] [«inst_1» : PartialOrder.{u_1} 𝕜] [«inst_2» : AddCommMonoid.{u_5} β] [«inst_3» : PartialOrder.{u_5} β] [«inst_4» : SMul.{u_1, u_5} 𝕜 β] {s : Set β} (hs : Convex 𝕜 s) : ConcaveOn 𝕜 s _root_.id := ⟨hs, by
    intros
    rfl⟩

run_cmd do
  let env ← Lean.getEnv
  let some old := env.find? `CompressionEvalSignatureOriginal | throwError "missing original comparison declaration"
  let some fresh := env.find? `CompressionEvalSignatureMaterialized | throwError "missing materialized comparison declaration"
  unless old.levelParams.length == fresh.levelParams.length do throwError "universe parameter count changed"
  let oldType := old.type.instantiateLevelParams old.levelParams (fresh.levelParams.map Lean.Level.param)
  unless Lean.Kernel.isDefEqGuarded env {} oldType fresh.type do throwError "materialized signature differs from original"
