# The capture contract — what a shot list owes a camera

> Loaded only when the shot list declares `capture_mode: live_action`. On a rendered list none of
> this applies: there is no camera to lock, no take to cover, and no continuity to log — the
> render re-derives the frame every time. `gtm_core.shots_lint` refuses the capture fields on a
> rendered list for exactly that reason.

A rendered shot list is a set of instructions to a model. A live-action shot list is a set of
instructions to a **person with a camera and a finite amount of time**, and the failures are
different in kind. A render that comes out wrong can be re-rendered for credits. A shoot that
comes out wrong is a second shoot: the light has changed, the shirt is in the wash, and the thing
that was on the desk has been put away.

Everything below exists to make the second shoot unnecessary.

---

## The seven capture laws

**1. Lock off anything that will be composited.** If two pieces of footage have to sit in the same
frame — an insert, a replacement, a screen keyed into a monitor, anything cut or matted together —
the camera does not move for either. A handheld plate and a handheld insert will never line up,
and no stabiliser fixes it afterwards: stabilisation crops and warps, so it changes the very
geometry the composite depends on. One tripod shot beats an hour in post.

**2. Leave a dead zone.** Decide, before rolling, which part of the frame stays empty — captions,
a lower third, a platform's own UI, a logo. Then keep the subject out of it. A dead zone is
cheaper than a reframe, and a reframe on a vertical cut is usually a crop into someone's face.

**3. Over-shoot every beat.** Roll before the action starts and keep rolling after it ends. Three
takes of a ten-second beat costs thirty seconds on the day; one take costs a second shoot when the
audio clips, a car goes past, or the performer blinks on the line. Coverage is not perfectionism —
it is the only insurance a shoot has.

**4. Keep a frame margin.** Shoot a little wider than the delivery frame. Every downstream step
takes some of the edge: a reframe to a second ratio, a stabilise pass, a burn-in that needs
somewhere to sit. A shot framed exactly to the deliverable has no margin to give, and the first
thing to go is the top of a head.

**5. Log continuity as you go, not afterwards.** What is on the desk, which hand holds the thing,
which way the jacket is buttoned, where the light is coming from. A note written between takes
takes five seconds. Reconstructing it from footage in the edit takes an hour and is often wrong —
and a continuity break is the defect viewers notice without being able to name.

**6. Plan the shoot backwards from the composite.** Work out what the finished frame needs, then
what has to be filmed to make it, then the order to film it in. Plates first, inserts second,
performance last — because a performance take shot before its plate exists is a take that may
have to be redone once the plate reveals the framing was wrong.

**7. One move per shot.** A push and a pan and a rack focus in one take is three chances to get it
wrong and no way to use the good parts separately. Give each move its own shot; the edit can
always cut between them, and it can never uncombine them.

---

## The seam laws — where two pieces of footage meet

A cut is the one moment the audience can see the machinery, so the seams get their own rules.

- **Match the light across a seam.** Two shots joined in the edit read as one moment only if the
  light agrees. Shoot the pair in the same session, or write the lighting into the continuity log
  and reproduce it. Colour can be graded; direction cannot.

- **Match the eyeline.** If a shot looks off-screen left, the shot it cuts to must respect that
  direction. Reversing it makes the two shots read as two different places, which is either a
  mistake or an effect — and it is only ever the second on purpose.

- **Leave handles at every seam.** A second of usable footage before and after each intended cut.
  The edit will move the cut. It always does.

- **A composite seam needs a clean plate.** Film the scene once with nothing in the place the
  composited element will go. It costs one take and it is the difference between a clean matte and
  an evening of rotoscoping.

---

## Writing this into the shot list

Four fields carry the contract, and each one is legal only under `capture_mode: live_action`:

| field | what it records |
|---|---|
| `lockoff` | `true` when the camera must not move for this shot — required in practice on anything composited |
| `dead_zone` | which part of the frame stays empty, and what will sit there |
| `coverage` | how many takes, and whether a clean plate is needed |
| `frame_margin` | how much wider than the deliverable to shoot |

Render the phone-readable version before the shoot — the operator reads this on set, not the JSON:

```bash
uv run python -m gtm_core.shots_lint <slug>.shots.json --render-shotlist
```

It writes `<slug>.shotlist.md` beside the JSON, one block per shot: the setup, the camera, the
lock-off, the dead zone, the coverage, the line, and the continuity notes. It is **generated** —
edit the JSON and re-render, never the markdown, which nothing reads back.
