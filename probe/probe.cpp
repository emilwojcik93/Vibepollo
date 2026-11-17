// Standalone probe of the touch_ux COM calls. Writes progress to probe.log after each stage.
#include <winsock2.h>
#include <windows.h>
#include <objbase.h>
#include <uiautomationclient.h>
#include <wrl/client.h>
#include <cstdio>
#include <cstdlib>
#include <string>
using Microsoft::WRL::ComPtr;
static const GUID clsid_cuiautomation = {0xff48dba4, 0x60ef, 0x4201, {0xaa, 0x87, 0x54, 0x10, 0x3e, 0xef, 0x59, 0x4e}};
static const GUID iid_iuiautomation = {0x30cbe57d, 0xd9d0, 0x452a, {0xab, 0x13, 0x7a, 0xc5, 0xac, 0x48, 0x25, 0xee}};
static const GUID iid_value_pattern = {0xa94cd8b1, 0x0844, 0x4cd6, {0x9d, 0x2d, 0x64, 0x05, 0x37, 0xab, 0x39, 0xe9}};
static const GUID clsid_tip_invocation = {0x4ce576fa, 0x83dc, 0x4f88, {0x95, 0x1c, 0x9d, 0x07, 0x82, 0xb4, 0xe3, 0x76}};
static const GUID iid_tip_invocation = {0x37c994e7, 0x432b, 0x4834, {0xa2, 0xf7, 0xdc, 0xe1, 0xf1, 0x3b, 0x83, 0x4b}};
static const GUID clsid_input_pane = {0xd5120aa3, 0x46ba, 0x44c5, {0x82, 0x2d, 0xca, 0x80, 0x92, 0xc1, 0xfc, 0x72}};
static const GUID iid_input_pane = {0x5752238b, 0x24f0, 0x495a, {0x82, 0xf1, 0x2f, 0xd5, 0x93, 0x05, 0x67, 0x96}};
struct ITipInvocationLocal: IUnknown { virtual HRESULT STDMETHODCALLTYPE Toggle(HWND wnd) = 0; };
struct IFrameworkInputPaneLocal: IUnknown {
  virtual HRESULT STDMETHODCALLTYPE Advise(IUnknown *window, IUnknown *handler, DWORD *cookie) = 0;
  virtual HRESULT STDMETHODCALLTYPE AdviseWithHWND(HWND wnd, IUnknown *handler, DWORD *cookie) = 0;
  virtual HRESULT STDMETHODCALLTYPE Unadvise(DWORD cookie) = 0;
  virtual HRESULT STDMETHODCALLTYPE Location(RECT *rect) = 0;
};
static FILE *lg;
static void L(const char *s, long v = 0) { fprintf(lg, "%s %ld\n", s, v); fflush(lg); }
int main(int argc, char **argv) {
  lg = fopen("C:\\Users\\Public\\posture-deploy\\probe.log", "w");
  bool sta = argc < 2 || std::string(argv[1]) != "mta";
  L(sta ? "mode sta" : "mode mta");
  HRESULT co = CoInitializeEx(nullptr, sta ? COINIT_APARTMENTTHREADED : COINIT_MULTITHREADED);
  L("coinit", co);
  // stage 2: input pane
  {
    ComPtr<IFrameworkInputPaneLocal> pane;
    HRESULT hr = CoCreateInstance(clsid_input_pane, nullptr, CLSCTX_INPROC_SERVER, iid_input_pane, reinterpret_cast<void **>(pane.GetAddressOf()));
    L("inputpane create", hr);
    if (SUCCEEDED(hr)) { RECT r {}; hr = pane->Location(&r); L("inputpane location hr", hr); L("rect w", r.right - r.left); L("rect h", r.bottom - r.top); }
  }
  // stage 3: UIA focused element
  {
    ComPtr<IUIAutomation> a;
    HRESULT hr = CoCreateInstance(clsid_cuiautomation, nullptr, CLSCTX_INPROC_SERVER, iid_iuiautomation, reinterpret_cast<void **>(a.GetAddressOf()));
    L("uia create", hr);
    if (SUCCEEDED(hr)) {
      ComPtr<IUIAutomationElement> e;
      hr = (argc > 3) ? a->ElementFromHandle(reinterpret_cast<UIA_HWND>(static_cast<intptr_t>(std::strtoll(argv[3], nullptr, 10))), e.GetAddressOf()) : a->GetFocusedElement(e.GetAddressOf());
      L("uia focused hr", hr);
      if (SUCCEEDED(hr) && e) {
        CONTROLTYPEID t = 0; hr = e->get_CurrentControlType(&t); L("controltype hr", hr); L("controltype", t);
        ComPtr<IUIAutomationValuePattern> v;
        hr = e->GetCurrentPatternAs(UIA_ValuePatternId, iid_value_pattern, reinterpret_cast<void **>(v.GetAddressOf()));
        L("valuepattern hr", hr);
        L("valuepattern ptr non-null", v ? 1 : 0); if (SUCCEEDED(hr) && v) { L("calling readonly"); BOOL ro = 0; hr = v->get_CurrentIsReadOnly(&ro); L("readonly hr", hr); L("readonly", ro); }
      }
    }
  }
  // stage 4: toggle twice
  if (argc > 2 && std::string(argv[2]) == "toggle") {
    for (int i = 0; i < 2; ++i) {
      ComPtr<ITipInvocationLocal> tip;
      HRESULT hr = CoCreateInstance(clsid_tip_invocation, nullptr, CLSCTX_LOCAL_SERVER, iid_tip_invocation, reinterpret_cast<void **>(tip.GetAddressOf()));
      L("tip create", hr);
      if (SUCCEEDED(hr)) { hr = tip->Toggle(GetDesktopWindow()); L("tip toggle hr", hr); }
      Sleep(2500);
    }
  }
  L("done");
  CoUninitialize();
  return 0;
}
