"""Dashboard panel for the live line monitor (Real mode only)."""
import time

from src.simulation.live_monitor import LineMonitor
from src.simulation.scenarios import ALL_NAMES

FPS = 8
MAX_FRAMES_PER_RUN = 600


def live_line_active(session):
    return getattr(session, 'live_line', None) is not None


def show_live_line(st, session):
    if session.mode != 'real' or not hasattr(session.environment, 'render_rgb'):
        return
    st.subheader('Live line monitor')
    st.caption('A simulated gantry builds the assembly while HoloGuide watches. A deterministic motion watchdog '
               'inspects only once the scene has been still for 3 frames. A BLOCK, review, hold or a pick refused '
               'by the order guard stops the line; only an operator action restarts it. Faults are scripted '
               'gantry mis-picks, recorded in the audit log.')
    line = getattr(session, 'live_line', None)
    if line is None:
        name = st.selectbox('Line scenario', ALL_NAMES, key='live_scenario')
        fresh = (session.workflow.last_decision is None and session.revision == 0
                 and getattr(session, 'scenario_runner', None) is None)
        if st.button('Start line', disabled=not fresh, type='primary'):
            session.live_line = LineMonitor(session.config, session.environment, session.workflow, name,
                                            session.directory / 'live-line')
            session.live_play = True
            st.rerun()
        if not fresh:
            st.info('Use "Start selected mode / reset session" first: the line needs a fresh session.')
        return

    view = st.empty()
    if getattr(session, 'live_play', False) and line.line == 'running':
        frame = None
        for _ in range(MAX_FRAMES_PER_RUN):
            frame = line.tick()
            view.image(frame.image, caption=f'Scenario: {line.scenario}', width=720)
            if line.line != 'running':
                break
            time.sleep(1 / FPS)
        session.live_play = False
        session.live_frame = frame.image if frame else None
    if getattr(session, 'live_frame', None) is None:
        session.live_frame = line.tick().image
    view.image(session.live_frame, caption=f'Scenario: {line.scenario}', width=720)

    if line.line == 'complete':
        st.success(f'Assembly complete. Decisions: {", ".join(line.decisions)}. '
                   'Reset the session to run another line.')
        return
    if line.line == 'running':
        if st.button('Resume line'):
            session.live_play = True
            st.rerun()
        return

    st.error(f'LINE STOPPED: {line.message}')
    actions = line.available_actions()
    operator = st.text_input('Operator identifier', key='live_operator')

    def act(fn, *args):
        if not operator.strip():
            st.warning('Enter an operator identifier first.')
            return
        try:
            fn(*args)
        except (ValueError, RuntimeError) as exc:
            st.error(str(exc))
            return
        session.live_play = True
        st.rerun()

    if 'correct' in actions and st.button('Correct the fault (gantry removes the faulty item), then re-inspect'):
        act(lambda: line.correct(operator.strip()))
    if 'acknowledge' in actions and st.button('Acknowledge the interlock and continue with the allowed part'):
        act(lambda: line.acknowledge(operator.strip()))
    reason = st.text_input('Reason (required for override or clearing a hold)', key='live_reason') \
        if {'override', 'clear_hold'} & set(actions) else ''
    if 'clear_hold' in actions and st.button('Clear hold and re-inspect'):
        if reason.strip():
            act(lambda: line.clear_hold(operator.strip(), reason.strip()))
        else:
            st.warning('A typed reason is required.')
    if 'override' in actions and st.button('Override: approve this step (logged)'):
        if reason.strip():
            act(lambda: line.override(True, reason.strip(), operator.strip()))
        else:
            st.warning('A typed reason is required.')
