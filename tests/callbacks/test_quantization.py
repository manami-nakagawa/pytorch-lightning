# Copyright The PyTorch Lightning team.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
import copy

import pytest
import torch

from pytorch_lightning import seed_everything, Trainer
from pytorch_lightning.callbacks import QuantizationAwareTraining
from pytorch_lightning.metrics.functional.mean_relative_error import mean_relative_error
from pytorch_lightning.utilities.exceptions import MisconfigurationException
from tests.helpers.datamodules import RegressDataModule
from tests.helpers.simple_models import RegressionModel


@pytest.mark.parametrize("observe", ['histogram', 'average'])
@pytest.mark.parametrize("fuse", [True, False])
def test_quantization(tmpdir, observe, fuse):
    """Parity for  quant model"""
    seed_everything(42)
    dm = RegressDataModule()
    trainer_args = dict(
        default_root_dir=tmpdir,
        max_epochs=10,
    )
    model = RegressionModel()
    qmodel = copy.deepcopy(model)

    Trainer(**trainer_args).fit(model, datamodule=dm)
    org_size = model.model_size
    org_score = torch.mean(torch.tensor([mean_relative_error(model(x), y) for x, y in dm.test_dataloader()]))

    fusing_layers = [(f'layer_{i}', f'layer_{i}a') for i in range(3)] if fuse else None
    qcb = QuantizationAwareTraining(observer_type=observe, modules_to_fuse=fusing_layers)
    Trainer(callbacks=[qcb], **trainer_args).fit(qmodel, datamodule=dm)

    quant_calls = qcb._forward_calls
    quant_size = qmodel.model_size
    quant_score = torch.mean(torch.tensor([mean_relative_error(qmodel(x), y) for x, y in dm.test_dataloader()]))
    assert quant_calls == qcb._forward_calls

    # test that the trained model is smaller then initial
    size_ratio = quant_size / org_size
    assert size_ratio < 0.65
    # test that the test score is almost the same as with pure training
    diff_score = abs(org_score - quant_score)
    assert diff_score < 0.35


def test_quantize_torchscript(tmpdir):
    """Test converting to torchscipt """
    dm = RegressDataModule()
    qmodel = RegressionModel()
    qcb = QuantizationAwareTraining(input_compatible=False)
    Trainer(callbacks=[qcb], default_root_dir=tmpdir, max_epochs=1).fit(qmodel, datamodule=dm)

    qmodel.to_torchscript()


def test_quantization_exceptions(tmpdir):
    """Test  wrong fuse layers"""
    with pytest.raises(MisconfigurationException, match='Unsupported qconfig'):
        QuantizationAwareTraining(qconfig=['abc'])

    with pytest.raises(MisconfigurationException, match='Unsupported observer type'):
        QuantizationAwareTraining(observer_type='abc')

    with pytest.raises(MisconfigurationException, match='Unsupported `collect_quantization`'):
        QuantizationAwareTraining(collect_quantization='abc')

    with pytest.raises(MisconfigurationException, match='Unsupported `collect_quantization`'):
        QuantizationAwareTraining(collect_quantization=1.2)

    fusing_layers = [(f'layers.mlp_{i}', f'layers.NONE-mlp_{i}a') for i in range(3)]
    qcb = QuantizationAwareTraining(modules_to_fuse=fusing_layers)
    trainer = Trainer(callbacks=[qcb], default_root_dir=tmpdir, max_epochs=1)
    with pytest.raises(MisconfigurationException, match='one or more of them is not your model attributes'):
        trainer.fit(RegressionModel(), datamodule=RegressDataModule())


def custom_trigger_never(trainer):
    return False


def custom_trigger_even(trainer):
    return trainer.current_epoch % 2 == 0


def custom_trigger_last(trainer):
    return trainer.current_epoch == (trainer.max_epochs - 1)


@pytest.mark.parametrize(
    "trigger_fn,expected_count", [
        (None, 9),
        (3, 3),
        (custom_trigger_never, 0),
        (custom_trigger_even, 5),
        (custom_trigger_last, 2),
    ]
)
def test_quantization_triggers(tmpdir, trigger_fn, expected_count):
    """Test  how many times the quant is called"""
    dm = RegressDataModule()
    qmodel = RegressionModel()
    qcb = QuantizationAwareTraining(collect_quantization=trigger_fn)
    Trainer(
        callbacks=[qcb],
        default_root_dir=tmpdir,
        limit_train_batches=1,
        limit_val_batches=1,
        max_epochs=4,
    ).fit(qmodel, datamodule=dm)

    assert qcb._forward_calls == expected_count
