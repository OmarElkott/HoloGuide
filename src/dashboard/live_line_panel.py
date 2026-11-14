"""Dashboard panel for the live line monitor (Real mode only)."""
import time

from src.dashboard.offline_check import run_check
from src.simulation.live_monitor import LineMonitor, Shift
from src.simulation.scenarios import ALL_NAMES
from src.vision.outage import ai_outage_active, set_ai_outage

FPS = 8
OPERATOR_DELAY_S = 2.0   # scripted operator waits this long before acting
FRAMES_PER_CHUNK = 160      # the page reruns between chunks, so buttons stay responsive


def live_line_active(session):
    return getattr(session, 'live_line', None) is not None


def show_deployment_status(st, session):
    """Offline self-test badge and the simulated AI-outage switch."""
    if 'offline_report' not in st.session_state or st.button('Re-run offline self-test'):
        st.session_state.offline_report = run_check(session.endpoint, session.model)
    report = st.session_state.offline_report
    box = st.success if report.verdict == 'OFFLINE VERIFIED' else st.warning
    box(f'**{report.verdict}** - ' + ' | '.join(report.details))
    inspector = session.workflow.inspector
    supported = hasattr(inspector, 'gemma') or type(getattr(inspector, 'inner', None)).__name__ == 'GemmaInspector'
    if supported:
        wanted = st.toggle('Simulate AI outage (Gemma unreachable)', value=ai_outage_active(inspector),
                           help='Swaps Gemma for an unreachable endpoint. The real failure paths run: known parts '
                                'stay on OpenCV, anything that needs Gemma goes to review or HOLD, never PASS.')
        if wanted != ai_outage_active(inspector):
            set_ai_outage(inspector, wanted)
            session.audit.record('ai_outage_simulated', active=wanted)
            st.rerun()


def _kpi_board(st, line):
    if not isinstance(line, Shift):
        return
    k = line.kpis()
    cols = st.columns(6)
    cols[0].metric('Units done', f"{k['units_completed']}/{k['units_planned']}")
    cols[1].metric('Faults caught', f"{k['faults_caught']}/{k['faults_injected']}")
    cols[2].metric('Faults escaped', k['faults_escaped'])
    cols[3].metric('False stops', k['false_stops'])
    cols[4].metric('Mean cycle (s)', k['mean_cycle_s'] if k['mean_cycle_s'] is not None else '-')
    cols[5].metric('Units / hour', k['units_per_hour'] if k['units_per_hour'] is not None else '-')
    if line.scripted_operator:
        st.caption('Operator actions in this shift are SCRIPTED (labelled on each frame); downtime per stop '
                   'is the scripted 2 s delay, not a measured human response.')


def show_live_line(st, session):
    if session.mode != 'real' or not hasattr(session.environment, 'render_rgb'):
        return
    st.subheader('Live line monitor')
    show_deployment_status(st, session)
    st.caption('A simulated gantry builds the assembly while HoloGuide watches. A deterministic motion watchdog '
               'inspects only once the scene has been still for 3 frames. A BLOCK, review, hold, a pick refused '
               'by the order guard, or the E-STOP stops the line; only an operator action restarts it. '
               'Faults are scripted gantry mis-picks, recorded in the audit log.')
    line = getattr(session, 'live_line', None)
    if line is None:
        _start_controls(st, session)
        return

    operator = st.text_input('Operator identifier', key='live_operator')
    kpi_box = st.empty()
    view = st.empty()
    if line.line == 'running' and st.button('E-STOP', type='primary', use_container_width=False):
        line.estop(operator.strip() or 'dashboard-operator', 'E-STOP button pressed')
        session.live_play = False
    if getattr(session, 'live_play', False) and line.line == 'running':
        frame = None
        for i in range(FRAMES_PER_CHUNK):
            frame = line.tick()
            view.image(frame.image, caption=f'Scenario: {line.scenario}', width=720)
            if i % 10 == 0:
                with kpi_box.container():
                    _kpi_board(st, line)
            if line.line != 'running':
                break
            time.sleep(1 / FPS)
        session.live_frame = frame.image if frame else None
        if line.line == 'running':
            st.rerun()                     # next chunk; keeps the E-STOP button live
        session.live_play = False
    if getattr(session, 'live_frame', None) is None:
        session.live_frame = line.tick().image
    view.image(session.live_frame, caption=f'Scenario: {line.scenario}', width=720)
    with kpi_box.container():
        _kpi_board(st, line)

    if line.line == 'complete':
        st.success(f'Line complete. Last unit decisions: {", ".join(line.decisions)}. '
                   'Reset the session to run again.')
        return
    if line.line == 'running':
        if st.button('Resume line'):
            session.live_play = True
            st.rerun()
        return
    _stopped_controls(st, session, line, operator)


def _start_controls(st, session):
    mode = st.radio('Run', ['Single scenario', 'Whole shift'], horizontal=True, key='live_mode')
    fresh = (session.workflow.last_decision is None and session.revision == 0
             and getattr(session, 'scenario_runner', None) is None)
    if mode == 'Single scenario':
        name = st.selectbox('Line scenario', ALL_NAMES, key='live_scenario')
    else:
        cols = st.columns(4)
        units = cols[0].number_input('Units', 2, 50, 10, key='shift_units')
        rate = cols[1].slider('Fault rate', 0.0, 1.0, 0.4, 0.05, key='shift_rate')
        seed = cols[2].number_input('Seed', 0, 9999, 0, key='shift_seed')
        scripted = cols[3].checkbox('Scripted operator', True, key='shift_scripted',
                                    help='Corrections are applied automatically after 2 s and labelled '
                                         'SCRIPTED OPERATOR on screen. Untick to act yourself.')
    if st.button('Start line', disabled=not fresh, type='primary'):
        if mode == 'Single scenario':
            session.live_line = LineMonitor(session.config, session.environment, session.workflow, name,
                                            session.directory / 'live-line')
        else:
            session.live_line = Shift(session.config, session.environment, session.workflow.inspector,
                                      session.audit, session.directory / 'shift', units=int(units),
                                      fault_rate=float(rate), seed=int(seed), fps=FPS,
                                      scripted_operator=scripted, operator_delay_s=OPERATOR_DELAY_S)
        session.live_play = True
        st.rerun()
    if not fresh:
        st.info('Use "Start selected mode / reset session" first: the line needs a fresh session.')


def _stopped_controls(st, session, line, operator):
    label = 'E-STOP ACTIVE' if 'release_estop' in line.available_actions() else 'LINE STOPPED'
    st.error(f'{label}: {line.message}')
    st.caption(f'AI handoff: {line.handoff}')
    actions = line.available_actions()
    needs_reason = {'override', 'clear_hold', 'release_estop'} & set(actions)
    reason = st.text_input('Reason (required to override, clear a hold or release the E-stop)',
                           key='live_reason') if needs_reason else ''

    def act(fn, *, with_reason=False):
        if not operator.strip():
            st.warning('Enter an operator identifier first.')
            return
        if with_reason and not reason.strip():
            st.warning('A typed reason is required.')
            return
        try:
            fn()
        except (ValueError, RuntimeError) as exc:
            st.error(str(exc))
            return
        session.live_play = True
        st.rerun()

    if 'correct' in actions and st.button('Correct the fault (gantry removes the faulty item), then re-inspect'):
        act(lambda: line.correct(operator.strip()))
    if 'acknowledge' in actions and st.button('Acknowledge the interlock and continue with the allowed part'):
        act(lambda: line.acknowledge(operator.strip()))
    if 'release_estop' in actions and st.button('Release E-stop'):
        act(lambda: line.release_estop(operator.strip(), reason.strip()), with_reason=True)
    if 'clear_hold' in actions and st.button('Clear hold and re-inspect'):
        act(lambda: line.clear_hold(operator.strip(), reason.strip()), with_reason=True)
    if 'override' in actions and st.button('Override: approve this step (logged)'):
        act(lambda: line.override(True, reason.strip(), operator.strip()), with_reason=True)
