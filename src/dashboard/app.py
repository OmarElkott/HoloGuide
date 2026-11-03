"""Launch: python -m streamlit run src/dashboard/app.py."""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import streamlit as st
from src.dashboard.backends import load_factory
from src.dashboard.demo import DemoController
from src.dashboard.presentation import STATE_BANNERS, camera_image, filter_audit
from src.dashboard.session import DashboardSession


def show_session(session, *, controls=True):
    workflow = session.workflow
    completed = (len(session.config.steps) if workflow.state.value == 'complete'
                 else workflow.current_step_id - 1)
    st.progress(completed / len(session.config.steps),
                text=f'{completed}/{len(session.config.steps)} steps verified')
    level, banner = STATE_BANNERS[workflow.state.value]
    getattr(st, level)(banner)
    st.subheader(f'Current step {workflow.current_step_id}: {workflow.current_step.name}')
    st.write(workflow.current_step.instruction)
    decision = workflow.last_decision
    if decision:
        st.write(f'Last decision: {decision.status.value} — inspected step {decision.step_id}')
        getattr(st, level)(decision.message)
    st.subheader('Camera image')
    image, placeholder = camera_image(session.frame)
    if image:
        st.image(image, caption='Current scene snapshot from the inspection render interface')
    else:
        st.info(placeholder)
    if session.inspected_frame and session.inspected_frame != session.frame:
        with st.expander(f'Last inspected image — step {session.inspected_step_id}'):
            old_image, old_placeholder = camera_image(session.inspected_frame)
            if old_image:
                st.image(old_image)
            else:
                st.info(old_placeholder)
    if session.latency_s is not None:
        st.metric('Last inspection latency', f'{session.latency_s:.3f} s')
        st.caption('Workflow request through decision and audit completion; excludes rendering and UI updates.')
    if controls:
        part = st.selectbox('Part', [p.name for p in session.config.parts])
        operator = st.text_input('Operator identifier')
        reason = st.text_input('Typed reason (required for override or removal)')
        valid_identity = bool(operator.strip() and reason.strip())
        revision = session.revision
        try:
            if st.button('Place part', disabled=workflow.state.value in {'complete', 'hold', 'inspecting'}):
                check = session.place(part)
                if check.allowed:
                    st.rerun()
                st.error(check.reason)
            if st.button('Remove part', disabled=not valid_identity or workflow.state.value == 'inspecting'):
                session.remove(part, reason, operator)
                st.rerun()
            if st.button('Refresh camera'):
                session.render()
                st.rerun()
            if st.button('Inspect', key=f'inspect-{revision}',
                         disabled=workflow.state.value in {'complete', 'hold', 'inspecting'} or workflow.retry_limit_reached):
                with st.spinner('Inspecting…'):
                    session.inspect(revision)
                st.rerun()
            if workflow.state.value in {'blocked', 'needs_review'}:
                approve = st.checkbox('Approve current step')
                if st.button('Submit override', key=f'override-{revision}', disabled=not valid_identity):
                    session.override(approve, reason, operator, revision)
                    st.rerun()
            if workflow.state.value == 'hold':
                if st.button('Acknowledge resolved hold', disabled=not valid_identity):
                    workflow.clear_hold(operator, reason)
                    session.revision += 1
                    st.rerun()
        except (ValueError, RuntimeError, OSError) as exc:
            st.error(str(exc))
    if decision:
        st.subheader(f'Decision details — step {decision.step_id}')
        st.write(f'Decided by: {decision.decided_by.value}')
        if decision.observation:
            st.metric('Observation confidence', f'{decision.observation.confidence:.0%}')
            st.write(decision.observation.rationale)
            st.json(decision.observation.model_dump(mode='json'))
    st.subheader('Session audit')
    try:
        events = filter_audit(session.audit.read_all())
        if not events:
            st.info('No audit events yet.')
        kinds = sorted({event['event_type'] for event in events})
        selected = st.multiselect('Event types (empty shows all)', kinds)
        st.json(filter_audit(events, selected))
        st.download_button('Download session audit', '\n'.join(json.dumps(e) for e in events),
                           file_name='audit.jsonl', mime='application/x-ndjson')
    except (ValueError, RuntimeError, OSError) as exc:
        st.error(f'Audit unavailable: {exc}')


@st.fragment(run_every=.75)
def play_demo():
    demo = st.session_state.get('demo')
    if not demo:
        return
    if st.button('Stop scripted demo', disabled=not demo.running):
        demo.stop()
    if demo.running:
        with st.spinner(f'Running mock episode: {demo.stages[demo.index]}'):
            demo.tick()
    st.progress(demo.index / len(demo.stages), text='Scripted mock demonstration progress')
    if demo.error:
        st.error(demo.error)
    if demo.index == len(demo.stages):
        st.success('Mock demonstration complete. Final real evaluation numbers remain unavailable.')
    for item in demo.history:
        with st.expander(item['stage']):
            st.code(item['detail'], language=None)
            st.caption(f"Persisted audit: {item['audit_path']}")
    if demo.session:
        show_session(demo.session, controls=False)


def main():
    st.set_page_config(page_title='HoloGuide', layout='wide')
    st.title('HoloGuide assembly dashboard')
    if 'assembly' not in st.session_state:
        st.session_state.assembly = DashboardSession()
    session = st.session_state.assembly
    mode = st.selectbox('Operation', ['Mock', 'Real'])
    if st.button('Start selected mode / reset session'):
        try:
            if mode == 'Real':
                factory = os.getenv('HOLOGUIDE_BACKEND_FACTORY')
                if not factory:
                    raise ValueError('Real backend unavailable: integrate the P1 environment and P2 inspector, '
                                     'then configure HOLOGUIDE_BACKEND_FACTORY.')
                replacement = load_factory(factory)
            else:
                replacement = DashboardSession()
            session.close()
            if st.session_state.get('demo'):
                st.session_state.demo.close()
                del st.session_state['demo']
            st.session_state.assembly = replacement
            st.rerun()
        except (ValueError, ImportError, AttributeError, OSError, RuntimeError) as exc:
            st.error(str(exc))
    if session.mode == 'mock':
        st.warning('Mock operation — deterministic JSON scenes; not real AI performance.')
    else:
        st.info('Real operation — configured teammate backend')
    st.caption(f'Inspector: {type(session.workflow.inspector).__name__}; '
               f'model: {session.model or "not configured"}; endpoint: {session.endpoint or "none configured"}. '
               'Network activity is not monitored. Localhost inference does not establish absence of other traffic.')
    if st.button('Run scripted mock demo'):
        if st.session_state.get('demo'):
            st.session_state.demo.close()
        st.session_state.demo = DemoController()
        st.rerun()
    if st.session_state.get('demo'):
        st.warning('Scripted MOCK episodes: normal, missing/wrong/order faults, correction, review, '
                   'injected timeout, recovery, and a logged demonstration override. No real model timing.')
        play_demo()
    else:
        show_session(session)


if __name__ == '__main__':
    main()
