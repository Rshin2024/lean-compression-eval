

theorem _root_.CompressionEvalSignatureOriginal (hf : ConvexOn 𝕜 s f) (hfg : EqOn f g s) : ConvexOn 𝕜 s g := ⟨hf.1, fun x hx y hy a b ha hb hab => by
    simpa only [← hfg hx, ← hfg hy, ← hfg (hf.1 hx hy ha hb hab)] using hf.2 hx hy ha hb hab⟩

theorem _root_.CompressionEvalSignatureMaterialized {«𝕜» : Type u_1} {«E» : Type u_2} {«β» : Type u_5} [«inst» : Semiring.{u_1} 𝕜] [«inst_1» : PartialOrder.{u_1} 𝕜] [«inst_2» : AddCommMonoid.{u_2} E] [«inst_3» : AddCommMonoid.{u_5} β] [«inst_4» : PartialOrder.{u_5} β] [«inst_5» : SMul.{u_1, u_2} 𝕜 E] [«inst_6» : SMul.{u_1, u_5} 𝕜 β] {«s» : Set.{u_2} E} {«f» : E → β} {«g» : E → β} (hf : ConvexOn 𝕜 s f) (hfg : EqOn f g s) : ConvexOn 𝕜 s g := ⟨hf.1, fun x hx y hy a b ha hb hab => by
    simpa only [← hfg hx, ← hfg hy, ← hfg (hf.1 hx hy ha hb hab)] using hf.2 hx hy ha hb hab⟩

run_cmd do
  let env ← Lean.getEnv
  let some old := env.find? `CompressionEvalSignatureOriginal | throwError "missing original comparison declaration"
  let some fresh := env.find? `CompressionEvalSignatureMaterialized | throwError "missing materialized comparison declaration"
  unless old.levelParams.length == fresh.levelParams.length do throwError "universe parameter count changed"
  let oldType := old.type.instantiateLevelParams old.levelParams (fresh.levelParams.map Lean.Level.param)
  unless Lean.Kernel.isDefEqGuarded env {} oldType fresh.type do throwError "materialized signature differs from original"
