

theorem _root_.CompressionEvalSignatureOriginal (f g : M →ₗ[R] N)
    {S : Set M} (hM : span R S = ⊤) : f = g ↔ ∀ (s : S), f s = g s := by
  convert!
    linearMap_eq_iff_of_eq_span (f.comp (Submodule.subtype _)) (g.comp (Submodule.subtype _))
      hM.symm
  constructor
  · rintro rfl
    rfl
  · intro h
    ext x
    exact DFunLike.congr_fun h ⟨x, by simp⟩

theorem _root_.CompressionEvalSignatureMaterialized {«R» : Type u_1} {«M» : Type u_4} [«inst» : Semiring.{u_1} R] [«inst_1» : AddCommMonoid.{u_4} M] [«inst_2» : Module.{u_1, u_4} R M] {«N» : Type u_8} [«inst_3» : AddCommMonoid.{u_8} N] [«inst_4» : Module.{u_1, u_8} R N] (f g : M →ₗ[R] N)
    {S : Set M} (hM : span R S = ⊤) : f = g ↔ ∀ (s : S), f s = g s := by
  convert!
    linearMap_eq_iff_of_eq_span (f.comp (Submodule.subtype _)) (g.comp (Submodule.subtype _))
      hM.symm
  constructor
  · rintro rfl
    rfl
  · intro h
    ext x
    exact DFunLike.congr_fun h ⟨x, by simp⟩

run_cmd do
  let env ← Lean.getEnv
  let some old := env.find? `CompressionEvalSignatureOriginal | throwError "missing original comparison declaration"
  let some fresh := env.find? `CompressionEvalSignatureMaterialized | throwError "missing materialized comparison declaration"
  unless old.levelParams.length == fresh.levelParams.length do throwError "universe parameter count changed"
  let oldType := old.type.instantiateLevelParams old.levelParams (fresh.levelParams.map Lean.Level.param)
  unless Lean.Kernel.isDefEqGuarded env {} oldType fresh.type do throwError "materialized signature differs from original"
