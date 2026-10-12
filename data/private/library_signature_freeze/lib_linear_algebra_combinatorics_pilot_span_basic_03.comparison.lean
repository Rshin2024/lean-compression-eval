

theorem _root_.CompressionEvalSignatureOriginal [RingHomSurjective σ₁₂] (f : M →ₛₗ[σ₁₂] M₂) (s : Set M) : (span R s).map f = span R₂ (f '' s) := Eq.symm <| span_eq_of_le _ (Set.image_mono subset_span) (image_span_subset_span f s)

theorem _root_.CompressionEvalSignatureMaterialized {«R» : Type u_1} {«R₂» : Type u_2} {«M» : Type u_4} {«M₂» : Type u_5} [«inst» : Semiring.{u_1} R] [«inst_1» : AddCommMonoid.{u_4} M] [«inst_2» : Module.{u_1, u_4} R M] [«inst_3» : Semiring.{u_2} R₂] {«σ₁₂» : RingHom.{u_1, u_2} R R₂} [«inst_4» : AddCommMonoid.{u_5} M₂] [«inst_5» : Module.{u_2, u_5} R₂ M₂] [RingHomSurjective σ₁₂] (f : M →ₛₗ[σ₁₂] M₂) (s : Set M) : (span R s).map f = span R₂ (f '' s) := Eq.symm <| span_eq_of_le _ (Set.image_mono subset_span) (image_span_subset_span f s)

run_cmd do
  let env ← Lean.getEnv
  let some old := env.find? `CompressionEvalSignatureOriginal | throwError "missing original comparison declaration"
  let some fresh := env.find? `CompressionEvalSignatureMaterialized | throwError "missing materialized comparison declaration"
  unless old.levelParams.length == fresh.levelParams.length do throwError "universe parameter count changed"
  let oldType := old.type.instantiateLevelParams old.levelParams (fresh.levelParams.map Lean.Level.param)
  unless Lean.Kernel.isDefEqGuarded env {} oldType fresh.type do throwError "materialized signature differs from original"
