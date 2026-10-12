

theorem _root_.CompressionEvalSignatureOriginal (v) : b.repr.symm v = Finsupp.linearCombination R b v := calc
    b.repr.symm v = b.repr.symm (v.sum Finsupp.single) := by simp
    _ = v.sum fun i vi => b.repr.symm (Finsupp.single i vi) := map_finsuppSum ..
    _ = Finsupp.linearCombination R b v := by simp only [repr_symm_single,
                                                         Finsupp.linearCombination_apply]

theorem _root_.CompressionEvalSignatureMaterialized {«ι» : Type u_1} {«R» : Type u_3} {«M» : Type u_6} [«inst» : Semiring.{u_3} R] [«inst_1» : AddCommMonoid.{u_6} M] [«inst_2» : Module.{u_3, u_6} R M] («b» : Module.Basis.{u_1, u_3, u_6} ι R M) (v) : b.repr.symm v = Finsupp.linearCombination R b v := calc
    b.repr.symm v = b.repr.symm (v.sum Finsupp.single) := by simp
    _ = v.sum fun i vi => b.repr.symm (Finsupp.single i vi) := map_finsuppSum ..
    _ = Finsupp.linearCombination R b v := by simp only [repr_symm_single,
                                                         Finsupp.linearCombination_apply]

run_cmd do
  let env ← Lean.getEnv
  let Option.some old := env.find? `CompressionEvalSignatureOriginal | throwError "missing original comparison declaration"
  let Option.some fresh := env.find? `CompressionEvalSignatureMaterialized | throwError "missing materialized comparison declaration"
  unless old.levelParams.length == fresh.levelParams.length do throwError "universe parameter count changed"
  let oldType := old.type.instantiateLevelParams old.levelParams (fresh.levelParams.map Lean.Level.param)
  unless Lean.Kernel.isDefEqGuarded env {} oldType fresh.type do throwError "materialized signature differs from original"
