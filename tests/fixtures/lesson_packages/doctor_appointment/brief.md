# Unit brief: Make or reschedule a doctor's appointment

This is a unit-level brief. It describes the situation and the relationship
between two lesson candidates; it is not itself a lesson.

## Unit outcome

The learner can participate in a short call to a doctor's office, explain the
reason for calling, request an appointment, understand an offered day and time,
respond when that option is unavailable, propose another option, and make the
final arrangement clear.

The interaction can branch. These are capabilities the learner may need in the
call, not a script or a required sequence of lines.

The unit deliberately keeps one grammar lesson and one communicative lesson
separate.

## Lesson candidates

### Grammar lesson: Norwegian main-clause word order

Source package: `grammar/`

Source slug: `question_word_order`

This is a reusable grammar ingredient. It explains and practises:

- finite verb before the subject in a direct question: `Snakker du norsk?`;
- the infinitive after `kan`: `Kan jeg få litt vann?`, not `Kan jeg får ...?`;
- finite-verb second position after a fronted phrase: `I dag jobber jeg hjemme.`;
- question-word order: `Hvor jobber du?`.

Its job is to make the form understandable and controllable. It does not teach
the appointment call or use that situation as its grammar context.

### Communicative lesson: Make and reschedule the appointment

Source package: `communicative/`

This lesson uses the grammar ingredient as a prerequisite and applies a useful
question form in an appointment call. It presents a complete model exchange,
then compares replies for different conditions: the offered time works, does
not work, or was not heard clearly.

It may use the grammar lesson's phrases as language resources, but it does not
repeat the finite-verb or V2 explanation. If the learner needs that explanation,
the grammar lesson is the correct place to provide it.

## Shared situation and roles

- **Learner:** a patient calling a doctor's office.
- **Partner:** a receptionist who asks what the call is about and offers a
  possible appointment.
- **Changed condition:** Thursday is offered first, but it does not work; the
  learner proposes Friday and makes the resulting arrangement clear.
- **Channel:** a short ordinary phone call, with no written script during the
  interaction.

## Editorial relationship

The grammar package is the reusable prerequisite. The communicative package is
the situation-specific application. The source files remain independently
editable: changing the grammar explanation should not require rewriting the
appointment dialogue, and changing the dialogue should not turn the grammar
lesson into an appointment lesson.

## Pilot boundary

The current compiler supports deterministic closed exercises. The final
communicative task is therefore a constrained transfer proxy, not an open
role-play or evidence of sustained free conversation.
